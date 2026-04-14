"""Thin client for the Anthropic Managed Agents API (beta).

The Managed Agents API exposes two primary resources:

* ``/v1/agents``   — durable agent definitions (system prompt, tools, MCP)
* ``/v1/sessions`` — per-run conversation with persistent state + event stream

This module wraps the REST endpoints with httpx. It deliberately stays
close to the raw API so that when the beta surface shifts we can update one
file rather than an abstraction.

CLI usage::

    python -m backend.managed_agents register agents/email-triage.json
    python -m backend.managed_agents list
    python -m backend.managed_agents run <agent_id> "Triage my inbox"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

import httpx

from .config import settings

BETA_HEADER = "managed-agents-2025-10-01"  # beta flag — bump when spec changes


def _headers() -> dict[str, str]:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": BETA_HEADER,
        "content-type": "application/json",
    }


class ManagedAgents:
    """Synchronous helper for the agent / session lifecycle."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.anthropic_api_base).rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=60.0)

    # --- Agents ----------------------------------------------------------

    def register(self, definition: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post("/v1/agents", headers=_headers(), json=definition)
        r.raise_for_status()
        return r.json()

    def list_agents(self) -> list[dict[str, Any]]:
        r = self._client.get("/v1/agents", headers=_headers())
        r.raise_for_status()
        return r.json().get("data", [])

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        r = self._client.get(f"/v1/agents/{agent_id}", headers=_headers())
        r.raise_for_status()
        return r.json()

    # --- Sessions --------------------------------------------------------

    def create_session(
        self, agent_id: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"agent_id": agent_id}
        if metadata:
            payload["metadata"] = metadata
        r = self._client.post("/v1/sessions", headers=_headers(), json=payload)
        r.raise_for_status()
        return r.json()

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        params = {"agent_id": agent_id} if agent_id else None
        r = self._client.get("/v1/sessions", headers=_headers(), params=params)
        r.raise_for_status()
        return r.json().get("data", [])

    def send_event(
        self, session_id: str, content: str | list[dict[str, Any]]
    ) -> dict[str, Any]:
        body = (
            {"type": "user_message", "content": content}
            if isinstance(content, str)
            else {"type": "user_message", "content": content}
        )
        r = self._client.post(
            f"/v1/sessions/{session_id}/events", headers=_headers(), json=body
        )
        r.raise_for_status()
        return r.json()

    def events(self, session_id: str) -> list[dict[str, Any]]:
        r = self._client.get(
            f"/v1/sessions/{session_id}/events", headers=_headers()
        )
        r.raise_for_status()
        return r.json().get("data", [])

    # --- Streaming -------------------------------------------------------

    async def stream(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Server-sent events from a running session."""
        url = f"{self.base_url}/v1/sessions/{session_id}/stream"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=_headers()) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if payload == "[DONE]":
                        return
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue


# --- CLI ----------------------------------------------------------------


def _load_definition(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    # allow the definition file to reference shared MCP config
    if data.get("mcp_servers") == "@shared":
        mcp = json.loads(settings.mcp_config_path.read_text())
        data["mcp_servers"] = mcp["servers"]
    return data


def _cli(argv: Iterable[str]) -> int:
    args = list(argv)
    if not args:
        print(__doc__)
        return 1
    mgr = ManagedAgents()
    cmd, rest = args[0], args[1:]
    if cmd == "register":
        for f in rest:
            out = mgr.register(_load_definition(f))
            print(json.dumps(out, indent=2))
    elif cmd == "list":
        for a in mgr.list_agents():
            print(f"{a['id']}\t{a.get('name','?')}")
    elif cmd == "run":
        agent_id, message = rest[0], " ".join(rest[1:])
        session = mgr.create_session(agent_id, metadata={"source": "cli"})
        print(f"session: {session['id']}")
        mgr.send_event(session["id"], message)
    else:
        print(f"unknown command: {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
