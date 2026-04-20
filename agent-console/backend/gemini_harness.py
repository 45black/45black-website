"""Gemini adapter — drop-in alternative to managed_agents.py.

The console frontend is harness-agnostic: it talks to ``backend/main.py``
which talks to whatever module implements the agent/session/event API.
This module implements that same shape on top of Gemini 3 Pro.

Deployment modes (auto-detected):

* **Local / Mac mini dev** — SQLite persistence + AI Studio free-tier API
  key (``GEMINI_API_KEY``). Shares quota with Gemini CLI.
* **Cloud Run** — Firestore persistence + Vertex AI via ADC. No secrets
  to manage beyond the service account. Triggered by ``K_SERVICE`` being
  set in the Cloud Run environment, or by setting
  ``GOOGLE_CLOUD_PROJECT`` explicitly.

The store choice is independent of the inference choice — you can mix
(e.g. Firestore + AI Studio key) by setting ``STORE_BACKEND`` and
``GEMINI_USE_VERTEX`` explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from google import genai
from google.genai import types

from .mcp_bridge import MCPBridge
from .store import AgentStore, make_store
from . import google_tools

log = logging.getLogger(__name__)

MAX_TOOL_TURNS = 20
BASH_ENABLED = os.environ.get("ALLOW_BASH_TOOL", "0") == "1"
BASH_TIMEOUT = int(os.environ.get("BASH_TIMEOUT", "30"))


# --- Client selection ------------------------------------------------------


def _use_vertex() -> bool:
    explicit = os.environ.get("GEMINI_USE_VERTEX")
    if explicit is not None:
        return explicit == "1"
    # Cloud Run sets K_SERVICE; GCP compute sets GCE_METADATA_HOST.
    return bool(os.environ.get("K_SERVICE") or os.environ.get("GOOGLE_CLOUD_PROJECT"))


def _make_client() -> genai.Client:
    """Choose Vertex AI (ADC) or AI Studio (API key) based on environment."""
    if _use_vertex():
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west1")
        if not project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT required when running in Vertex AI mode"
            )
        return genai.Client(vertexai=True, project=project, location=location)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Either set it "
            "(https://aistudio.google.com/app/apikey) "
            "or run on GCP compute with ADC + GOOGLE_CLOUD_PROJECT."
        )
    return genai.Client(api_key=api_key)


# --- Built-in tools --------------------------------------------------------


def _run_bash(command: str) -> dict[str, Any]:
    """Execute a shell command locally. Gated behind ALLOW_BASH_TOOL=1."""
    if not BASH_ENABLED:
        return {"error": "bash tool disabled — set ALLOW_BASH_TOOL=1"}
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT,
            cwd=str(Path.home()),
        )
        return {
            "stdout": proc.stdout[-4000:] if proc.stdout else "",
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"command timed out after {BASH_TIMEOUT}s"}
    except Exception as exc:
        return {"error": str(exc)}


BASH_DECLARATION = types.FunctionDeclaration(
    name="bash",
    description="Execute a bash command on the local machine. Use for file operations, data processing, and system tasks.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
        },
        "required": ["command"],
    },
)


# --- History conversion ----------------------------------------------------


def _events_to_contents(events: list[dict[str, Any]]) -> list[types.Content]:
    contents: list[types.Content] = []
    pending_fn_responses: list[types.Part] = []

    for evt in events:
        t = evt["type"]
        c = evt["content"]

        if t == "user":
            if pending_fn_responses:
                contents.append(types.Content(role="user", parts=pending_fn_responses))
                pending_fn_responses = []
            text = c if isinstance(c, str) else json.dumps(c)
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

        elif t == "assistant":
            if pending_fn_responses:
                contents.append(types.Content(role="user", parts=pending_fn_responses))
                pending_fn_responses = []
            text = c if isinstance(c, str) else json.dumps(c)
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))

        elif t == "tool_call":
            if pending_fn_responses:
                contents.append(types.Content(role="user", parts=pending_fn_responses))
                pending_fn_responses = []
            call_data = c if isinstance(c, dict) else json.loads(c)
            contents.append(
                types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name=call_data["name"],
                            args=call_data.get("args", {}),
                        )
                    ],
                )
            )

        elif t == "tool_result":
            result_data = c if isinstance(c, dict) else json.loads(c)
            output = result_data.get("output", result_data)
            if not isinstance(output, dict):
                output = {"result": str(output)}
            pending_fn_responses.append(
                types.Part.from_function_response(
                    name=result_data.get("name", "unknown"),
                    response=output,
                )
            )

    if pending_fn_responses:
        contents.append(types.Content(role="user", parts=pending_fn_responses))

    return contents


# --- Harness ---------------------------------------------------------------


@dataclass
class GeminiHarness:
    """Pluggable agent runtime targeting Gemini 3 Pro."""

    model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3-pro"))
    _client: genai.Client = field(default=None, repr=False)  # type: ignore[assignment]
    _mcp: MCPBridge = field(default=None, repr=False)  # type: ignore[assignment]
    _store: AgentStore = field(default=None, repr=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._client = _make_client()
        self._mcp = MCPBridge()
        self._store = make_store()

    # --- Agents ---------------------------------------------------------

    def register(self, definition: dict[str, Any]) -> dict[str, Any]:
        slug = definition.get("slug") or definition["name"].lower().replace(" ", "-")
        agent_id = f"agent_{uuid.uuid4().hex[:12]}"
        self._store.register_agent(agent_id, slug, definition)
        return {"id": agent_id, "slug": slug, **definition}

    def list_agents(self) -> list[dict[str, Any]]:
        return self._store.list_agents()

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self._store.get_agent(agent_id)

    # --- Sessions -------------------------------------------------------

    def create_session(
        self, agent_id: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        # Accept either agent_id or slug from the caller.
        if not agent_id.startswith("agent_"):
            agent = self._store.get_agent_by_slug(agent_id)
            if not agent:
                raise KeyError(f"unknown agent slug: {agent_id}")
            agent_id = agent["id"]
        self._store.create_session(sid, agent_id, metadata or {})
        return {"id": sid, "agent_id": agent_id, "status": "idle"}

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        return self._store.list_sessions(agent_id=agent_id)

    def events(self, session_id: str) -> list[dict[str, Any]]:
        return self._store.events(session_id)

    def send_event(
        self, session_id: str, content: str | list[dict[str, Any]]
    ) -> dict[str, Any]:
        self._store.append_event(session_id, "user", content)
        self._kick_turn(session_id)
        return {"ok": True, "session_id": session_id}

    async def stream(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        async for evt in self._store.stream_events(session_id):
            yield evt

    # --- Internals ------------------------------------------------------

    def _build_tools(self, agent_def: dict[str, Any]) -> list[types.Tool]:
        fn_declarations: list[types.FunctionDeclaration] = []
        tools: list[types.Tool] = []

        builtin_types = {t.get("type") for t in agent_def.get("tools", [])}

        if "bash" in builtin_types:
            fn_declarations.append(BASH_DECLARATION)

        if "web_search" in builtin_types:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        # Native Google Workspace tools (direct API, no MCP).
        if google_tools.available():
            for d in google_tools.DECLARATIONS:
                fn_declarations.append(
                    types.FunctionDeclaration(
                        name=d["name"],
                        description=d.get("description", ""),
                        parameters_json_schema=d.get("parameters"),
                    )
                )
            log.info("Google Workspace tools: %d registered", len(google_tools.DECLARATIONS))
        else:
            log.info("Google Workspace tools: unavailable (no OAuth token), falling back to MCP")

        # MCP tools (for anything not covered by native tools).
        for d in self._mcp.discover():
            if d["name"] in google_tools.TOOLS:
                continue  # native tool takes priority
            fn_declarations.append(
                types.FunctionDeclaration(
                    name=d["name"],
                    description=d.get("description", ""),
                    parameters_json_schema=d.get("parameters"),
                )
            )

        if fn_declarations:
            tools.append(types.Tool(function_declarations=fn_declarations))

        return tools

    def _execute_function_call(
        self, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if name == "bash":
            return _run_bash(args.get("command", ""))
        if name in google_tools.TOOLS:
            return google_tools.dispatch(name, args)
        return self._mcp.call(name, args)

    def _kick_turn(self, session_id: str) -> None:
        try:
            agent_def = self._store.agent_for_session(session_id)
        except KeyError as exc:
            self._store.append_event(session_id, "error", str(exc))
            self._store.set_session_status(session_id, "error")
            return

        system_prompt = agent_def.get("system_prompt", "")
        max_tokens = agent_def.get("max_tokens_per_turn", 4096)
        gemini_tools = self._build_tools(agent_def)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt if system_prompt else None,
            max_output_tokens=max_tokens,
            tools=gemini_tools if gemini_tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
            ),
        )

        for _ in range(MAX_TOOL_TURNS):
            contents = _events_to_contents(self._store.events(session_id))
            if not contents:
                self._store.set_session_status(session_id, "idle")
                return

            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except Exception as exc:
                log.error("Gemini API error: %s", exc)
                self._store.append_event(session_id, "error", f"Gemini API error: {exc}")
                self._store.set_session_status(session_id, "error")
                return

            if not response.candidates:
                self._store.append_event(session_id, "error", "No candidates in response")
                self._store.set_session_status(session_id, "error")
                return

            candidate = response.candidates[0]
            parts = candidate.content.parts if candidate.content else []

            text_chunks: list[str] = []
            fn_calls: list[tuple[str, dict[str, Any]]] = []
            for part in parts:
                if part.text:
                    text_chunks.append(part.text)
                if part.function_call:
                    fn_calls.append((
                        part.function_call.name,
                        dict(part.function_call.args) if part.function_call.args else {},
                    ))

            if text_chunks:
                self._store.append_event(session_id, "assistant", "\n".join(text_chunks))

            if not fn_calls:
                self._store.set_session_status(session_id, "idle")
                return

            for name, args in fn_calls:
                self._store.append_event(
                    session_id, "tool_call", {"name": name, "args": args}
                )
                result = self._execute_function_call(name, args)
                self._store.append_event(
                    session_id, "tool_result", {"name": name, "output": result}
                )

        self._store.append_event(
            session_id,
            "error",
            f"Agent hit {MAX_TOOL_TURNS}-turn tool limit — breaking loop.",
        )
        self._store.set_session_status(session_id, "idle")


# Drop-in alias so main.py can import either harness without branching.
ManagedAgents = GeminiHarness  # type: ignore[assignment]


# --- CLI ----------------------------------------------------------------


def _cli(argv: Iterable[str]) -> int:
    import sys

    args = list(argv)
    if not args:
        print(__doc__)
        return 1
    h = GeminiHarness()
    cmd, rest = args[0], args[1:]
    if cmd == "register":
        for f in rest:
            d = json.loads(Path(f).read_text())
            print(json.dumps(h.register(d), indent=2))
    elif cmd == "list":
        for a in h.list_agents():
            print(f"{a['id']}\t{a.get('slug','?')}\t{a.get('name','?')}")
    elif cmd == "run":
        if len(rest) < 2:
            print("usage: run <agent_id_or_slug> <message>", file=sys.stderr)
            return 2
        agent_ref, message = rest[0], " ".join(rest[1:])
        s = h.create_session(agent_ref, {"source": "cli"})
        print(f"session: {s['id']}")
        h.send_event(s["id"], message)
        for e in h.events(s["id"]):
            print(f"[{e['type']}] {e['content']}")
    else:
        print(f"unknown command: {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
