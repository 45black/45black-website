"""Gemini adapter — drop-in alternative to managed_agents.py.

The console frontend is harness-agnostic: it talks to ``backend/main.py``
which talks to whatever module implements the agent/session/event API.
This module implements that same shape on top of Gemini 3 Pro, with
persistence kept locally in SQLite so the droplet path doesn't rely on
any managed upstream.

Authentication uses the ``GEMINI_API_KEY`` environment variable. The free
AI Studio tier key shares quota with Gemini CLI. Generate one at
https://aistudio.google.com/app/apikey — no billing setup required for
the free tier.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from google import genai
from google.genai import types

from .config import settings
from .mcp_bridge import MCPBridge

log = logging.getLogger(__name__)

DB_PATH = Path(settings.agents_dir).parent / "harness.sqlite"

MAX_TOOL_TURNS = 20
BASH_ENABLED = os.environ.get("ALLOW_BASH_TOOL", "0") == "1"
BASH_TIMEOUT = int(os.environ.get("BASH_TIMEOUT", "30"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    definition_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_events_session_seq
    ON events(session_id, seq);
"""


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
    try:
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def _row_to_dict(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _make_client() -> genai.Client:
    """Build a genai.Client. Uses GEMINI_API_KEY env var (auto-detected by SDK)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Get a free key at "
            "https://aistudio.google.com/app/apikey"
        )
    return genai.Client(api_key=api_key)


# --- Built-in tools --------------------------------------------------------


def _run_bash(command: str) -> dict[str, Any]:
    """Execute a shell command locally. Gated behind ALLOW_BASH_TOOL=1."""
    if not BASH_ENABLED:
        return {"error": "bash tool disabled — set ALLOW_BASH_TOOL=1 in .env"}
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


def _events_to_contents(
    events: list[dict[str, Any]],
) -> list[types.Content]:
    """Rebuild Gemini-compatible conversation history from the event log."""
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
    """Local, SQLite-backed agent runtime targeting Gemini 3 Pro."""

    model: str = "gemini-3-pro"
    _client: genai.Client = field(default=None, repr=False)  # type: ignore[assignment]
    _mcp: MCPBridge = field(default=None, repr=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._client = _make_client()
        self._mcp = MCPBridge()

    # --- Agents ---------------------------------------------------------

    def register(self, definition: dict[str, Any]) -> dict[str, Any]:
        slug = definition.get("slug") or definition["name"].lower().replace(" ", "-")
        agent_id = f"agent_{uuid.uuid4().hex[:12]}"
        with _db() as c:
            c.execute(
                "INSERT OR REPLACE INTO agents(id, slug, definition_json, created_at) "
                "VALUES (?,?,?,?)",
                (agent_id, slug, json.dumps(definition), time.time()),
            )
        return {"id": agent_id, "slug": slug, **definition}

    def list_agents(self) -> list[dict[str, Any]]:
        with _db() as c:
            rows = c.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
        out = []
        for r in rows:
            d = json.loads(r["definition_json"])
            out.append({"id": r["id"], "slug": r["slug"], **d})
        return out

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        with _db() as c:
            r = c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not r:
            raise KeyError(agent_id)
        return {"id": r["id"], "slug": r["slug"], **json.loads(r["definition_json"])}

    # --- Sessions -------------------------------------------------------

    def create_session(
        self, agent_id: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with _db() as c:
            c.execute(
                "INSERT INTO sessions(id, agent_id, metadata_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (sid, agent_id, json.dumps(metadata or {}), now, now),
            )
        return {"id": sid, "agent_id": agent_id, "status": "idle"}

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with _db() as c:
            if agent_id:
                rows = c.execute(
                    "SELECT * FROM sessions WHERE agent_id = ? ORDER BY updated_at DESC",
                    (agent_id,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def events(self, session_id: str) -> list[dict[str, Any]]:
        with _db() as c:
            rows = c.execute(
                "SELECT type, content_json, created_at FROM events "
                "WHERE session_id = ? ORDER BY seq ASC",
                (session_id,),
            ).fetchall()
        return [
            {"type": r["type"], "content": json.loads(r["content_json"]), "ts": r["created_at"]}
            for r in rows
        ]

    def send_event(
        self, session_id: str, content: str | list[dict[str, Any]]
    ) -> dict[str, Any]:
        self._append_event(session_id, "user", content)
        self._kick_turn(session_id)
        return {"ok": True, "session_id": session_id}

    async def stream(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        last_seq = -1
        while True:
            with _db() as c:
                rows = c.execute(
                    "SELECT seq, type, content_json FROM events "
                    "WHERE session_id = ? AND seq > ? ORDER BY seq ASC",
                    (session_id, last_seq),
                ).fetchall()
            for r in rows:
                last_seq = r["seq"]
                yield {"type": r["type"], "content": json.loads(r["content_json"])}
            import asyncio
            await asyncio.sleep(0.5)

    # --- Internals ------------------------------------------------------

    def _append_event(self, session_id: str, type_: str, content: Any) -> int:
        now = time.time()
        with _db() as c:
            row = c.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = row["next"]
            c.execute(
                "INSERT INTO events(session_id, seq, type, content_json, created_at) "
                "VALUES (?,?,?,?,?)",
                (session_id, seq, type_, json.dumps(content), now),
            )
            c.execute(
                "UPDATE sessions SET updated_at = ?, status = 'running' WHERE id = ?",
                (now, session_id),
            )
        return seq

    def _load_agent_for_session(self, session_id: str) -> dict[str, Any]:
        with _db() as c:
            row = c.execute(
                "SELECT a.definition_json FROM sessions s "
                "JOIN agents a ON a.id = s.agent_id "
                "WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"no agent found for session {session_id}")
        return json.loads(row["definition_json"])

    def _build_tools(self, agent_def: dict[str, Any]) -> list[types.Tool]:
        """Build Gemini Tool objects from the agent definition + MCP discovery."""
        fn_declarations: list[types.FunctionDeclaration] = []
        tools: list[types.Tool] = []

        builtin_types = {t.get("type") for t in agent_def.get("tools", [])}

        if "bash" in builtin_types:
            fn_declarations.append(BASH_DECLARATION)

        if "web_search" in builtin_types:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        # Discover MCP tools and convert to Gemini FunctionDeclarations.
        mcp_decl_dicts = self._mcp.discover()
        for d in mcp_decl_dicts:
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
        """Route a function call to bash or MCP."""
        if name == "bash":
            return _run_bash(args.get("command", ""))
        return self._mcp.call(name, args)

    def _kick_turn(self, session_id: str) -> None:
        """Run one or more Gemini inference turns until the model stops calling tools."""
        try:
            agent_def = self._load_agent_for_session(session_id)
        except KeyError as exc:
            self._append_event(session_id, "error", str(exc))
            self._set_status(session_id, "error")
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

        for turn in range(MAX_TOOL_TURNS):
            history = self.events(session_id)
            contents = _events_to_contents(history)

            if not contents:
                self._set_status(session_id, "idle")
                return

            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                log.error("Gemini API error: %s", exc)
                self._append_event(session_id, "error", f"Gemini API error: {exc}")
                self._set_status(session_id, "error")
                return

            if not response.candidates:
                self._append_event(session_id, "error", "No candidates in response")
                self._set_status(session_id, "error")
                return

            candidate = response.candidates[0]
            parts = candidate.content.parts if candidate.content else []

            # Collect text and function calls from this response.
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

            # Emit text if any.
            if text_chunks:
                self._append_event(session_id, "assistant", "\n".join(text_chunks))

            # No tool calls → model is done.
            if not fn_calls:
                self._set_status(session_id, "idle")
                return

            # Execute each function call, persist results, and loop.
            for name, args in fn_calls:
                self._append_event(
                    session_id, "tool_call", {"name": name, "args": args}
                )
                result = self._execute_function_call(name, args)
                self._append_event(
                    session_id,
                    "tool_result",
                    {"name": name, "output": result},
                )

            # Loop: feed results back to Gemini on next iteration.

        # Exhausted tool turns.
        self._append_event(
            session_id,
            "error",
            f"Agent hit {MAX_TOOL_TURNS}-turn tool limit — breaking loop.",
        )
        self._set_status(session_id, "idle")

    def _set_status(self, session_id: str, status: str) -> None:
        with _db() as c:
            c.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), session_id),
            )


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
            print("usage: run <agent_id> <message>", file=sys.stderr)
            return 2
        agent_id, message = rest[0], " ".join(rest[1:])
        s = h.create_session(agent_id, {"source": "cli"})
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
