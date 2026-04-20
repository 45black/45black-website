"""Firestore-backed AgentStore for Cloud Run deployment.

Collections:

* ``agents``   — doc id = agent_id, stores full definition
* ``sessions`` — doc id = session_id, includes agent_id + status
* ``sessions/{sid}/events`` — subcollection, doc id is the sequence number
  (zero-padded string so lexicographic ordering matches numeric)

Uses Application Default Credentials. On Cloud Run that's the attached
service account, no key file needed.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, AsyncIterator

from google.cloud import firestore

DATABASE_ID = os.environ.get("FIRESTORE_DATABASE", "(default)")
SEQ_WIDTH = 8  # supports 100M events per session


def _pad(seq: int) -> str:
    return f"{seq:0{SEQ_WIDTH}d}"


class FirestoreStore:
    backend_name = "firestore"

    def __init__(self) -> None:
        self._db = firestore.Client(database=DATABASE_ID)

    # --- Agents ---------------------------------------------------------

    def register_agent(self, agent_id: str, slug: str, definition: dict[str, Any]) -> None:
        self._db.collection("agents").document(agent_id).set(
            {
                "id": agent_id,
                "slug": slug,
                "definition": definition,
                "created_at": time.time(),
            }
        )

    def list_agents(self) -> list[dict[str, Any]]:
        docs = (
            self._db.collection("agents")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .stream()
        )
        return [self._agent_doc_to_dict(d.to_dict()) for d in docs]

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        doc = self._db.collection("agents").document(agent_id).get()
        if not doc.exists:
            raise KeyError(agent_id)
        return self._agent_doc_to_dict(doc.to_dict())

    def get_agent_by_slug(self, slug: str) -> dict[str, Any] | None:
        hits = (
            self._db.collection("agents")
            .where(filter=firestore.FieldFilter("slug", "==", slug))
            .limit(1)
            .stream()
        )
        for d in hits:
            return self._agent_doc_to_dict(d.to_dict())
        return None

    @staticmethod
    def _agent_doc_to_dict(raw: dict[str, Any]) -> dict[str, Any]:
        return {"id": raw["id"], "slug": raw["slug"], **(raw.get("definition") or {})}

    # --- Sessions -------------------------------------------------------

    def create_session(
        self, session_id: str, agent_id: str, metadata: dict[str, Any]
    ) -> None:
        now = time.time()
        self._db.collection("sessions").document(session_id).set(
            {
                "id": session_id,
                "agent_id": agent_id,
                "status": "idle",
                "metadata": metadata,
                "created_at": now,
                "updated_at": now,
                "next_seq": 0,
            }
        )

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        q = self._db.collection("sessions").order_by(
            "updated_at", direction=firestore.Query.DESCENDING
        )
        if agent_id:
            q = q.where(filter=firestore.FieldFilter("agent_id", "==", agent_id))
        return [d.to_dict() for d in q.stream()]

    def agent_for_session(self, session_id: str) -> dict[str, Any]:
        sess = self._db.collection("sessions").document(session_id).get()
        if not sess.exists:
            raise KeyError(f"no session {session_id}")
        agent = self.get_agent(sess.to_dict()["agent_id"])
        return {k: v for k, v in agent.items() if k not in ("id", "slug")}

    def set_session_status(self, session_id: str, status: str) -> None:
        self._db.collection("sessions").document(session_id).update(
            {"status": status, "updated_at": time.time()}
        )

    # --- Events ---------------------------------------------------------

    def append_event(self, session_id: str, type_: str, content: Any) -> int:
        now = time.time()
        session_ref = self._db.collection("sessions").document(session_id)

        @firestore.transactional
        def _txn(txn: firestore.Transaction) -> int:
            snap = session_ref.get(transaction=txn)
            seq = int(snap.get("next_seq") or 0)
            txn.update(
                session_ref,
                {"next_seq": seq + 1, "updated_at": now, "status": "running"},
            )
            txn.set(
                session_ref.collection("events").document(_pad(seq)),
                {
                    "seq": seq,
                    "type": type_,
                    "content": content,
                    "created_at": now,
                },
            )
            return seq

        return _txn(self._db.transaction())

    def events(self, session_id: str) -> list[dict[str, Any]]:
        docs = (
            self._db.collection("sessions")
            .document(session_id)
            .collection("events")
            .order_by("seq")
            .stream()
        )
        out = []
        for d in docs:
            raw = d.to_dict()
            out.append(
                {"type": raw["type"], "content": raw["content"], "ts": raw["created_at"]}
            )
        return out

    async def stream_events(
        self, session_id: str, poll_s: float = 1.0
    ) -> AsyncIterator[dict[str, Any]]:
        """Polling-based SSE source.

        Firestore has a native listen() API but it needs a background thread
        that's awkward to surface as an async generator on Cloud Run. Polling
        at 1s is cheap (few reads / session / second, all inside free tier).
        """
        last_seq = -1
        events_ref = (
            self._db.collection("sessions").document(session_id).collection("events")
        )
        while True:
            q = events_ref.where(
                filter=firestore.FieldFilter("seq", ">", last_seq)
            ).order_by("seq")
            for d in q.stream():
                raw = d.to_dict()
                last_seq = raw["seq"]
                yield {"type": raw["type"], "content": raw["content"]}
            await asyncio.sleep(poll_s)
