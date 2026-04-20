# 45black Agent Console

A small self-hostable console for building, viewing and running persistent
agents for the 45black business — email triage, calendar, ad-hoc tasks.

> **Status:** scaffold. Wire in keys, creds and MCP servers before it's
> actually doing work. See `COSTS.md` for the honest trade-off between
> deployment paths.

## Two deployment paths

| Path | Compute | Persistence | Inference | Good for |
|------|---------|-------------|-----------|----------|
| **Cloud Run** (recommended) | GCP, scale to zero | Firestore | Vertex AI (Gemini 3 Pro via ADC, no API key) | Production — no always-on box, EU data residency, IAP-gated UI |
| **Local / Mac mini** | your box | SQLite | AI Studio free-tier API key | Development, or ultra-low-cost steady-state if you already run an always-on machine |

The same code runs in both modes. Three environment variables select the
shape:

```bash
AGENT_HARNESS=gemini              # or "claude" for Anthropic Managed Agents
STORE_BACKEND=sqlite              # or "firestore"; auto-detects on Cloud Run
GEMINI_USE_VERTEX=                # blank = auto-detect; "1" forces Vertex AI
```

## Layout

```
agent-console/
├── README.md
├── COSTS.md                      # cost model, both paths
├── requirements.txt
├── .env.example
├── backend/
│   ├── main.py                   # FastAPI app — agents / sessions / tick
│   ├── gemini_harness.py         # Gemini inference loop + tool loop
│   ├── managed_agents.py         # Anthropic Managed Agents client (opt-in)
│   ├── mcp_bridge.py             # MCP tool discovery + dispatch
│   ├── store.py                  # AgentStore protocol
│   ├── sqlite_store.py           # SQLite backend (local)
│   ├── firestore_store.py        # Firestore backend (Cloud Run)
│   └── config.py
├── frontend/                     # console UI (45black Saville palette)
├── agents/                       # agent JSON definitions
├── mcp/servers.json              # MCP server endpoints + allowlists
├── scripts/
│   ├── tick.py                   # scheduler entrypoint (cron / launchd)
│   ├── launchd.plist.example     # macOS always-on launcher
│   └── cloudflared-tunnel.md     # Mac-mini-hosted MCP tunnel setup
└── deploy/                       # Cloud Run deployment
    ├── README.md                 # step-by-step walkthrough
    ├── Dockerfile
    ├── cloudbuild.yaml
    ├── service.yaml
    ├── scheduler.yaml
    ├── apply-scheduler.sh
    └── terraform/main.tf
```

## Quickstart — Cloud Run

See **`deploy/README.md`** for the full walkthrough. Summary:

```bash
# 1. Create project + enable APIs + provision infra
export PROJECT_ID=45black-agents
cd deploy/terraform && terraform apply -var="project_id=$PROJECT_ID"

# 2. Build + deploy the console
cd ../..
gcloud builds submit --config deploy/cloudbuild.yaml

# 3. Register agents + wire scheduler
./deploy/apply-scheduler.sh        # sets up 30-min triage + 07:30 briefing
# (agent registration: see deploy/README.md step 6)

# 4. Put IAP in front of the console for day-to-day access
```

Estimated steady-state cost: **~£30–£45 / month** (Vertex AI inference
only — Cloud Run, Firestore, Scheduler, Secret Manager all fall inside
free tiers at this volume). New GCP projects get $300/90-day credit —
that's your first ~6 months covered.

## Quickstart — Local / Mac mini

```bash
cd agent-console
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# set AGENT_HARNESS=gemini, GEMINI_API_KEY=<your AI Studio key>,
# STORE_BACKEND=sqlite

python -m backend.gemini_harness register agents/email-triage.json
python -m backend.gemini_harness register agents/calendar-assistant.json

uvicorn backend.main:app --reload --port 8787
# open http://localhost:8787
```

Steady-state cost: **£0** inside Gemini AI Studio free-tier quota
(~1.5k requests/day on Pro). Keeps going until you bust the cap, then
spills to the paid API (~£30/month at this workload).

Persistence across reboots on macOS: copy
`scripts/launchd.plist.example` to
`~/Library/LaunchAgents/tech.45black.agent-console.plist` and
`launchctl load` it.

## MCP servers

The agents reach Gmail, Calendar and bespoke 45black tools through MCP
servers. Three options for where to host them:

1. **Sibling Cloud Run services** (recommended on the Cloud Run path) —
   containerised MCPs with the same service-account pattern.
2. **On the Mac mini via cloudflared tunnel** — still works, and lets
   tools hold local OAuth tokens / filesystem state. See
   `scripts/cloudflared-tunnel.md`.
3. **Skip MCP for Gmail/Calendar** — call Google Workspace APIs directly
   with a user OAuth refresh token in Secret Manager. Fewer moving parts
   if you're already on GCP.

`mcp/servers.json` carries both the tunnel URL and a `_local_dev_url`
sibling. Set `MCP_USE_LOCAL=1` locally to talk to MCPs on localhost.

## Choosing a harness

- **Gemini (default)** — Gemini 3 Pro via Vertex or AI Studio. Runs
  locally or on Cloud Run.
- **Claude Managed Agents** — Anthropic-hosted runtime, strong
  agentic quality. Flip `AGENT_HARNESS=claude` and set
  `ANTHROPIC_API_KEY`. No local persistence needed — sessions live
  server-side at Anthropic. See `COSTS.md` for when this is worth the
  extra cash.

## Security notes

- Local mode binds to `127.0.0.1`. Do **not** expose without auth.
- Cloud Run service is deployed with `--no-allow-unauthenticated`; put
  IAP in front for normal browser access.
- Secrets (`MCP_GATEWAY_TOKEN`, OAuth tokens) live in Secret Manager on
  GCP, in `.env` (gitignored) locally.
- `ALLOW_BASH_TOOL` is off by default. Only turn it on if you trust the
  model and the environment — on Cloud Run it means the agent can execute
  bash inside the container, which is sandboxed but not airtight.
