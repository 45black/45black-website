# Deploying the 45black Agent Console to Google Cloud

Target: Cloud Run + Firestore + Cloud Scheduler + Vertex AI (Gemini 3 Pro).
Region: `europe-west1` (Belgium) — EU data residency for pensions-sensitive work.

## Topology

```
           Cloud Scheduler (OIDC)
                   │
                   ▼
       ┌───────────────────────┐        Vertex AI (Gemini 3 Pro)
       │ Cloud Run: console    │──────► aiplatform.googleapis.com
       │  FastAPI + UI         │
       │  service account:     │        Firestore (agents, sessions, events)
       │  agent-console@…      │◄─────► firestore.googleapis.com
       └──────────┬────────────┘
                  │ Secret Manager: MCP token, OAuth refresh
                  ▼
            MCP servers
       (Cloud Run siblings, or
       Mac mini via cloudflared)
```

## One-off setup

### 1. Create the GCP project (skip if already exists)

```bash
export PROJECT_ID=45black-agents        # pick your own
export REGION=europe-west1
gcloud projects create "$PROJECT_ID" --name="45black agents"
gcloud config set project "$PROJECT_ID"
gcloud billing projects link "$PROJECT_ID" \
    --billing-account=$(gcloud billing accounts list \
    --filter=open=true --format='value(ACCOUNT_ID)' | head -1)
```

### 2. Provision everything with Terraform

```bash
cd deploy/terraform
terraform init
terraform apply -var="project_id=$PROJECT_ID"
```

That creates: Artifact Registry repo `agents`, Firestore database (Native
mode, `europe-west1`), service accounts `agent-console` and
`agent-scheduler`, IAM bindings, and empty Secret Manager secrets.

### 3. Populate secrets

```bash
echo -n "$(openssl rand -hex 32)" | \
    gcloud secrets versions add mcp-gateway-token --data-file=-

# If you're routing Gmail/Calendar via the Google Workspace MCP server,
# add the OAuth refresh token once you've done the one-off consent flow.
cat oauth_refresh.txt | \
    gcloud secrets versions add gmail-oauth-refresh-token --data-file=-
```

### 4. Build and deploy the console

```bash
# From the agent-console directory:
gcloud builds submit --config deploy/cloudbuild.yaml
```

First build takes ~4 minutes. Subsequent deploys run in ~90 seconds.

### 5. Wire the scheduler

```bash
PROJECT_ID=$PROJECT_ID ./deploy/apply-scheduler.sh
```

This creates two jobs:
- `email-triage` — every 30 min, Mon–Fri, 07–18 UK time
- `morning-briefing` — 07:30 Mon–Fri UK time

Let the scheduler's service account invoke the Cloud Run service:

```bash
gcloud run services add-iam-policy-binding console \
    --region="$REGION" \
    --member="serviceAccount:agent-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

### 6. Register the agents (one-off)

```bash
SERVICE_URL=$(gcloud run services describe console \
    --region="$REGION" --format='value(status.url)')

# Get an identity token for your user to call the authenticated service:
TOKEN=$(gcloud auth print-identity-token \
    --audiences="$SERVICE_URL")

for f in agents/*.json; do
  curl -X POST "$SERVICE_URL/api/agents/register" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d @"$f"
done
```

> **Note**: the `/api/agents/register` endpoint is a small addition —
> see `backend/main.py`. For now you can also register by running the CLI
> locally against the same Firestore database:
> `STORE_BACKEND=firestore GOOGLE_CLOUD_PROJECT=$PROJECT_ID python -m backend.gemini_harness register agents/email-triage.json`.

### 7. Open the console

Your user identity token lets you browse the UI:

```bash
# Open a URL with the token in a cookie (one-liner for dev):
open "$SERVICE_URL/?token=$TOKEN"
```

For day-to-day use, put the service behind Identity-Aware Proxy (IAP) so
your Google login gates access without fiddling with tokens:

```bash
gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service=console \
    --region="$REGION" \
    --member=user:you@example.com \
    --role=roles/iap.httpsResourceAccessor
```

## Iterate

Subsequent deploys: `gcloud builds submit --config deploy/cloudbuild.yaml`.
Roll back: `gcloud run services update-traffic console --to-revisions=PREV=100`.
Tail logs: `gcloud run services logs tail console --region="$REGION"`.

## Running locally against the same Firestore

Useful for debugging a problem you can reproduce with the production data:

```bash
export GOOGLE_CLOUD_PROJECT=$PROJECT_ID
export GEMINI_USE_VERTEX=1
export STORE_BACKEND=firestore
export AGENT_HARNESS=gemini
gcloud auth application-default login    # once
uvicorn backend.main:app --port 8787
```

## MCP servers

Two patterns for reaching Gmail / Calendar / CRM:

1. **Deploy MCPs as sibling Cloud Run services** — recommended for anything
   that doesn't need local filesystem access. Same service account pattern,
   same Firestore for state if needed.
2. **Keep MCPs on the Mac mini + cloudflared tunnel** — still works; just
   point `mcp/servers.json` at `https://mcp.45black.tech/...` and the
   console will call them from Cloud Run. Use the Secret Manager
   `mcp-gateway-token` as the bearer.

For Gmail/Calendar specifically, consider **skipping MCP** and calling the
Google Workspace APIs directly from the console with the service account +
domain-wide delegation (for G Suite) or a user OAuth refresh token (for
personal Gmail). Simpler auth, fewer moving parts. See
`backend/google_tools.py` (TODO — not yet written; raise an issue if
you want this scaffolded).

## Cost notes

At the workload in `../COSTS.md` (72 ticks/day, 30-min cadence, weekdays):

| Line | Monthly |
|------|---------|
| Cloud Run (min=0, ~2h CPU/mo) | £0 (inside free tier) |
| Firestore (~15k writes, 50k reads/mo) | £0 (inside free tier) |
| Cloud Scheduler (2 jobs) | £0 (first 3 free) |
| Secret Manager (<6 secrets) | £0 (first 6 free) |
| Artifact Registry (1 image, ~300 MB) | ~£0.01 |
| Vertex AI Gemini 3 Pro | ~£30–£45 (same tokens as AI Studio) |
| **Total** | **~£30–£45 / month** |

First project gets $300 / 90-day free credit — covers the first ~6 months
of inference outright. After that, Vertex AI is the only real line item
and it's priced identically to AI Studio's paid tier.

## Tearing down

```bash
cd deploy/terraform
terraform destroy -var="project_id=$PROJECT_ID"

# Then manually delete the Cloud Run service (not managed by TF above):
gcloud run services delete console --region="$REGION"
```
