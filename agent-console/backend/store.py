"""Persistence protocol shared by SQLite (local) and Firestore (Cloud Run).

Both backends expose the same surface so ``gemini_harness.py`` doesn't
care where the state actually lives.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Protocol


class AgentStore(Protocol):
    """Minimal persistence surface. Two implementations: SQLite + Firestore."""

    def register_agent(self, agent_id: str, slug: str, definition: dict[str, Any]) -> None: ...

    def list_agents(self) -> list[dict[str, Any]]: ...

    def get_agent(self, agent_id: str) -> dict[str, Any]: ...

    def get_agent_by_slug(self, slug: str) -> dict[str, Any] | None: ...

    def create_session(
        self, session_id: str, agent_id: str, metadata: dict[str, Any]
    ) -> None: ...

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]: ...

    def agent_for_session(self, session_id: str) -> dict[str, Any]: ...

    def set_session_status(self, session_id: str, status: str) -> None: ...

    def append_event(self, session_id: str, type_: str, content: Any) -> int: ...

    def events(self, session_id: str) -> list[dict[str, Any]]: ...

    def stream_events(
        self, session_id: str, poll_s: float = 0.5
    ) -> AsyncIterator[dict[str, Any]]: ...


def make_store() -> AgentStore:
    """Pick the backend from STORE_BACKEND env var.

    * ``sqlite`` (default) — local file, suitable for dev and single-machine
      deployment. Auto-selected if ``STORE_BACKEND`` is unset AND
      ``K_SERVICE`` (Cloud Run marker) is not present.
    * ``firestore`` — Google Cloud Firestore, suitable for Cloud Run.
      Auto-selected if ``K_SERVICE`` is present.
    """
    backend = os.environ.get("STORE_BACKEND")
    if not backend:
        backend = "firestore" if os.environ.get("K_SERVICE") else "sqlite"

    if backend == "firestore":
        from .firestore_store import FirestoreStore
        return FirestoreStore()
    from .sqlite_store import SQLiteStore
    return SQLiteStore()
