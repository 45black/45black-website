"""FastAPI console backend.

Bind loopback only. Serves the static UI from /, and proxies a narrow slice
of the Managed Agents API to the frontend so the browser never sees the
Anthropic API key.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .managed_agents import ManagedAgents

app = FastAPI(title="45black Agent Console", version="0.1.0")
mgr = ManagedAgents()


# --- Static UI ---------------------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory=str(settings.frontend_dir)),
    name="static",
)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(settings.frontend_dir / "index.html")


# --- API ---------------------------------------------------------------------


@app.get("/api/agents")
def list_agents() -> dict[str, Any]:
    # Merge local agent definitions with anything already registered upstream.
    local: list[dict[str, Any]] = []
    for f in sorted(settings.agents_dir.glob("*.json")):
        data = json.loads(f.read_text())
        local.append(
            {
                "slug": f.stem,
                "name": data.get("name", f.stem),
                "description": data.get("description", ""),
                "model": data.get("model"),
            }
        )
    try:
        remote = mgr.list_agents()
    except Exception as exc:  # upstream may be offline / beta churn
        remote = []
        remote_error = str(exc)
    else:
        remote_error = None
    return {"local": local, "remote": remote, "remote_error": remote_error}


@app.get("/api/agents/{slug}")
def get_agent(slug: str) -> dict[str, Any]:
    f = settings.agents_dir / f"{slug}.json"
    if not f.exists():
        raise HTTPException(404, f"no such agent: {slug}")
    return json.loads(f.read_text())


@app.get("/api/sessions")
def list_sessions(agent_id: str | None = None) -> dict[str, Any]:
    return {"data": mgr.list_sessions(agent_id=agent_id)}


@app.post("/api/sessions")
def create_session(body: dict[str, Any]) -> dict[str, Any]:
    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(400, "agent_id required")
    return mgr.create_session(agent_id, metadata=body.get("metadata") or {})


@app.post("/api/sessions/{session_id}/events")
def send_event(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    content = body.get("content")
    if not content:
        raise HTTPException(400, "content required")
    return mgr.send_event(session_id, content)


@app.get("/api/sessions/{session_id}/events")
def get_events(session_id: str) -> dict[str, Any]:
    return {"data": mgr.events(session_id)}


@app.get("/api/sessions/{session_id}/stream")
async def stream(session_id: str) -> StreamingResponse:
    async def gen():
        try:
            async for evt in mgr.stream(session_id):
                yield f"data: {json.dumps(evt)}\n\n"
        except asyncio.CancelledError:  # client disconnect
            return

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "api_key_set": bool(settings.anthropic_api_key),
        "agents_dir": str(settings.agents_dir),
    }
