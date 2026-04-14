# 45black Agent Console

A minimal self-hosted console for building, viewing and running persistent
Claude agents — designed to run on a Mac mini (or any always-on box) and
handle email triage, calendar management and ad-hoc business tasks for 45black.

> **Status:** scaffold. Wire in your API key + MCP credentials before use.

## What this is

- **Backend:** small FastAPI app that talks to the Anthropic **Managed Agents**
  API (`/v1/agents`, `/v1/sessions`). Persistence, crash-recovery and event
  streaming are handled server-side by Anthropic — we don't run our own
  checkpointer.
- **Frontend:** static HTML/CSS/JS in the 45black visual style (IBM Plex,
  Saville palette). Lists agents, shows session history, streams live events.
- **Agents:** JSON definitions for Email Triage and Calendar Assistant, each
  wired to community MCP servers for Gmail / Google Calendar.
- **Scheduler:** `scripts/tick.py` — a single-shot entrypoint meant to be run
  from cron or `launchd` every N minutes to start a new triage session.

## Why not LangGraph / OpenHands / a DO droplet?

See `COSTS.md` for the full comparison. Short version: Managed Agents removes
most of the infrastructure work (state, recovery, event log), so the only
thing we host locally is the UI + scheduler + MCP servers with local
credentials.

## How the cloud agent reaches local tools

Managed Agents run in Anthropic's cloud, so anything they need to poke —
Gmail with your OAuth token, your CRM, a bespoke `45black-tools` MCP — has
to be reachable over HTTPS. We do this with a **Cloudflare tunnel**
(`cloudflared`) rather than opening a port on the Mac mini or standing up
a VPS.

- Origin: MCP servers on `localhost:879x` on the Mac mini.
- Edge: `mcp.45black.tech` on Cloudflare, gated by Cloudflare Access
  service tokens + a bearer token the MCP server itself checks.
- Cost: £0 on Cloudflare's free tier.

Full setup in `scripts/cloudflared-tunnel.md`. The MCP URLs in
`mcp/servers.json` already point at the public tunnel hostname and carry
`_local_dev_url` siblings for when you're working offline.

## Layout

```
agent-console/
├── README.md
├── COSTS.md                    # cost model + comparison
├── requirements.txt
├── .env.example
├── backend/
│   ├── main.py                 # FastAPI app: proxies to Managed Agents
│   ├── managed_agents.py       # thin REST client wrapper
│   └── config.py
├── frontend/
│   ├── index.html              # console UI (45black style)
│   ├── console.css
│   └── console.js
├── agents/
│   ├── email-triage.json
│   └── calendar-assistant.json
├── mcp/
│   └── servers.json            # Gmail + Calendar MCP server config
└── scripts/
    ├── tick.py                 # single-shot run (for cron)
    └── launchd.plist.example   # macOS always-on launcher
```

## Quickstart

```bash
cd agent-console
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env — set ANTHROPIC_API_KEY and Google OAuth creds

# register the agents with Anthropic (one-off)
python -m backend.managed_agents register agents/email-triage.json
python -m backend.managed_agents register agents/calendar-assistant.json

# run the console
uvicorn backend.main:app --reload --port 8787
# open http://localhost:8787
```

## Running persistently on a Mac mini

1. Copy `scripts/launchd.plist.example` to
   `~/Library/LaunchAgents/tech.45black.agent-console.plist`, edit the path,
   then `launchctl load` it. This keeps the FastAPI server up across reboots.
2. For periodic email triage, add the sample cron line from
   `scripts/tick.py` — it creates a new Managed Agents session every 10
   minutes and lets Claude triage new mail.

## Security notes

- The console binds to `127.0.0.1` by default. Do **not** expose it to the
  public internet without adding auth.
- MCP OAuth tokens live in `.env` (gitignored).
- `ANTHROPIC_API_KEY` should be scoped / rotated.
