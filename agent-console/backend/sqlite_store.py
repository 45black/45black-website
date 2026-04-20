"""SQLite-backed AgentStore. Used for local dev and single-machine deploy."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncIterator

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


class SQLiteStore:
    backend_name = "sqlite"

    def register_agent(self, agent_id: str, slug: str, definition: dict[str, Any]) -> None:
        with _db() as c:
            c.execute(
                "INSERT OR REPLACE INTO agents(id, slug, definition_json, created_at) "
                "VALUES (?,?,?,?)",
                (agent_id, slug, json.dumps(definition), time.time()),
            )

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

    def get_agent_by_slug(self, slug: str) -> dict[str, Any] | None:
        with _db() as c:
            r = c.execute("SELECT * FROM agents WHERE slug = ?", (slug,)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "slug": r["slug"], **json.loads(r["definition_json"])}

    def create_session(
        self, session_id: str, agent_id: str, metadata: dict[str, Any]
    ) -> None:
        now = time.time()
        with _db() as c:
            c.execute(
                "INSERT INTO sessions(id, agent_id, metadata_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (session_id, agent_id, json.dumps(metadata), now, now),
            )

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
        return [dict(r) for r in rows]

    def agent_for_session(self, session_id: str) -> dict[str, Any]:
        with _db() as c:
            row = c.execute(
                "SELECT a.definition_json FROM sessions s "
                "JOIN agents a ON a.id = s.agent_id "
                "WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"no agent for session {session_id}")
        return json.loads(row["definition_json"])

    def set_session_status(self, session_id: str, status: str) -> None:
        with _db() as c:
            c.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), session_id),
            )

    def append_event(self, session_id: str, type_: str, content: Any) -> int:
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

    async def stream_events(
        self, session_id: str, poll_s: float = 0.5
    ) -> AsyncIterator[dict[str, Any]]:
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
            await asyncio.sleep(poll_s)
