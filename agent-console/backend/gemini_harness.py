"""Gemini adapter — drop-in alternative to managed_agents.py.

The console frontend is harness-agnostic: it talks to `backend/main.py`
which talks to whatever module implements the agent/session/event API.
This module implements that same shape on top of Gemini 3 Pro, with
persistence kept locally in SQLite so the droplet path doesn't rely on
any managed upstream.

Design notes:

* Authentication prefers Google Application Default Credentials so you
  can use the `gcloud auth application-default login` flow that shares
  quota with Gemini CLI (free AI Studio tier). Falls back to
  ``GEMINI_API_KEY`` if set — that path bills the paid API.
* "Agent" = row in ``agents`` table, stores system prompt, tool config,
  MCP server refs.
* "Session" = row in ``sessions`` table with an append-only ``events``
  log. Each ``send_event`` call runs one Gemini turn, streams tokens,
  persists the assistant response, executes any tool/MCP calls, loops
  until the model stops or a token budget trips.
* Recovery: the event log is the source of truth. Restarting the
  process picks up mid-conversation from the last stored event.

This is a **sketch**, not production code. It shows the wiring so the
frontend can be pointed at either harness by flipping one import in
`backend/main.py`. Tool-call execution, MCP transport and Gemini's
thinking-mode knobs are left as TODOs marked in-line.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from .config import settings

DB_PATH = Path(settings.agents_dir).parent / "harness.sqlite"

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


@dataclass
class GeminiHarness:
    """Local, SQLite-backed agent runtime targeting Gemini 3 Pro."""

    model: str = "gemini-3-pro"

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
        # Fire-and-forget the inference turn; the stream() method will emit
        # assistant + tool events as they happen.
        self._kick_turn(session_id)
        return {"ok": True, "session_id": session_id}

    async def stream(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        # Poll the events table for new rows. SSE semantics — once the
        # client disconnects, the async iterator is cancelled.
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

    def _kick_turn(self, session_id: str) -> None:
        """Run one Gemini turn against the full session event history.

        TODO: replace the stub below with a real call to
        ``google.genai`` / Vertex AI. Pseudocode:

            from google import genai
            client = genai.Client()  # uses ADC → shares Gemini CLI quota
            history = self._to_gemini_history(self.events(session_id))
            stream = client.models.generate_content_stream(
                model=self.model, contents=history, tools=self._tools_for(session_id),
            )
            for chunk in stream:
                if chunk.text:
                    self._append_event(session_id, 'assistant_delta', chunk.text)
                for call in chunk.function_calls or []:
                    result = self._run_tool(call)  # MCP over HTTPS tunnel
                    self._append_event(session_id, 'tool_call', call.model_dump())
                    self._append_event(session_id, 'tool_result', result)
            self._finalise(session_id)

        Tool loop semantics (Gemini auto function calling is sufficient
        for most cases; for MCP we'll need a manual loop since the MCP
        transport isn't native to the Gemini SDK yet).
        """
        self._append_event(
            session_id,
            "assistant",
            "[stub] Gemini harness wired; real inference loop TODO — see _kick_turn docstring.",
        )
        with _db() as c:
            c.execute(
                "UPDATE sessions SET status = 'idle' WHERE id = ?", (session_id,)
            )


# Drop-in compatibility with managed_agents.ManagedAgents so backend/main.py
# can import either without branching. Swap the import in main.py to switch
# harnesses.
ManagedAgents = GeminiHarness  # type: ignore[assignment]


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
        agent_id, message = rest[0], " ".join(rest[1:])
        s = h.create_session(agent_id, {"source": "cli"})
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
