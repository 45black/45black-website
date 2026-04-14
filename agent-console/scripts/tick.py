#!/usr/bin/env python3
"""Single-shot tick: start a new Managed Agents session for a given agent slug.

Designed to be run from cron or launchd on the Mac mini. Creates a fresh
session, sends a short trigger event, and exits. The agent runs in Anthropic's
cloud from there; you watch the results from the web console.

Example crontab (every 10 min, weekdays 07:00–19:00):

    */10 7-19 * * 1-5  cd ~/45black-website/agent-console && \
        .venv/bin/python scripts/tick.py email-triage "Triage new mail"

Example crontab (weekday morning briefing at 07:30):

    30 7 * * 1-5  cd ~/45black-website/agent-console && \
        .venv/bin/python scripts/tick.py calendar-assistant "Morning briefing"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.managed_agents import ManagedAgents  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: tick.py <agent-slug> <trigger message>", file=sys.stderr)
        return 2
    slug, message = argv[0], " ".join(argv[1:])

    # Resolve slug → Managed Agents agent_id. We store the upstream id in
    # .agent-ids.json after registration so cron doesn't need to hit /v1/agents
    # on every tick (saves latency and a round-trip).
    ids_path = ROOT / ".agent-ids.json"
    if not ids_path.exists():
        print(
            "no .agent-ids.json — run `python -m backend.managed_agents register` first",
            file=sys.stderr,
        )
        return 1
    agent_ids = json.loads(ids_path.read_text())
    if slug not in agent_ids:
        print(f"slug {slug!r} not registered", file=sys.stderr)
        return 1

    mgr = ManagedAgents()
    session = mgr.create_session(
        agent_ids[slug],
        metadata={"source": "tick", "slug": slug},
    )
    mgr.send_event(session["id"], message)
    print(f"started session {session['id']} for {slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
