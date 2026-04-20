# Cost comparison — honest revision

> Previous revision of this doc overstated Managed Agents' case by ~4× and
> understated the droplet baseline. This version fixes both. Numbers are
> still indicative — check live pricing before signing off. Last revised
> April 2026.

## 1. The call Jon made on the first draft

On the first pass of this doc I wrote:

- **Claude Managed Agents: £70–£105/month**
- **Droplet + Gemini path: £75–£90/month cash, £575–£1 090 fully loaded**

Two problems with that framing, both flagged in review:

1. **Padded token model.** I assumed every triage tick processes a
   non-trivial queue (12k in / 2k out). In reality ~85% of ticks hit an
   empty inbox and do a cheap list-and-stop loop. Real monthly tokens are
   roughly 35–40% of what I claimed.

2. **Pretended sunk costs were new.** The £18/month DO droplet and £20/month
   Google AI subscription already exist and are paid for other reasons.
   For this decision the marginal cost of adding an agent loop to that
   infra is what matters, not the gross line item.

Both adjustments point the same direction: the droplet path is cheaper and
the gap is wider than I made out.

## 2. Honest workload assumption

72 triage ticks/day (every 10 min, 07–19, weekdays), 1 morning briefing,
~30 conversational turns/day, ~10 calendar operations.

Triage tick shape:

| Tick type | Share | Input | Output |
|-----------|-------|-------|--------|
| Empty inbox | ~85% | ~1.5k | ~0.2k |
| Light (1–3 msgs) | ~12% | ~5k | ~1k |
| Heavy backlog | ~3% | ~15k | ~3k |

Per working day (21/month):

- Triage: ~190k in / 40k out
- Morning briefing: 8k in / 3k out
- Ad-hoc conversation: 30 × 6k in / 1.5k out ≈ 180k in / 45k out
- Calendar ops: 10 × 5k in / 1k out ≈ 50k in / 10k out

**Monthly totals:**

- Input: **~9 M tokens**
- Output: **~2 M tokens**

(Previous revision claimed 23 M / 4.2 M. This is the honest number.)

## 3. Option A — Claude Managed Agents, honest

### Base: Sonnet 4.6 for everything

- Input: 9 × $3 = $27
- Output: 2 × $15 = $30
- **~$57 / month ≈ £45 / month**

### With prompt caching (~70% of input cacheable)

- Uncached input: 2.7 × $3 = $8
- Cached input: 6.3 × $0.30 = $1.90
- Output: $30
- **~$40 / month ≈ £32 / month**

### Smart routing — Haiku 4.5 for triage, Sonnet for conversation

Triage is ~60% of input by volume, mostly boring, ideal for Haiku 4.5.

- Triage input (Haiku, mostly cached): ~$2
- Triage output (Haiku): 1.2 M × $5/M = $6
- Conversation input (Sonnet, cached): ~$3
- Conversation output (Sonnet): 0.8 M × $15 = $12
- **~$23 / month ≈ £18 / month**

So the realistic Managed Agents bill sits in **£18–£45/month** depending on
how hard you tune model routing and caching.

### What's *not* included on top

- **Hosting:** £0 (Anthropic runs the harness)
- **Persistence / event log / recovery:** £0 (managed, in-beta freebie)
- **Cloudflare tunnel for local MCPs:** £0 (free tier)
- **Mac mini electricity:** marginal, already running

**Managed Agents marginal monthly: £18–£45.**

## 4. Option B — Existing droplet + Gemini 3 Pro (Jon's actual infra)

### The subscription question, properly answered

Google AI Ultra / One AI Premium / Workspace AI **do not** include unlimited
Gemini API calls. What they do give, relevant here:

1. **Gemini app + NotebookLM + Workspace sidebar access** — not usable for
   programmatic agent loops.
2. **Gemini CLI** with login auth — as of early 2026 this rides the free
   "AI Studio" tier tied to your Google account. Published quota is on the
   order of **1000 Gemini-Pro-class requests/day** with rate limits per
   minute. A 30-min triage cadence + briefings + conversation fits
   comfortably inside this for a single operator. Heavy backlog days on
   10-min cadence can clip the RPM cap.
3. **Vertex AI / AI Studio paid API** — separate billing, not bundled.

So if your agent harness authenticates via Gemini CLI / ADC against your
Google account, realistic marginal inference cost is **£0** up to quota,
occasional small overage if you go hard.

### Droplet

You already pay **£18/month** for other reasons. Adding an OpenHands or
custom harness process alongside whatever is already running there is
marginal RAM and CPU.

**Droplet path marginal monthly: £0–£5** (electricity on edge-cases plus
the occasional overage request into the paid Gemini API).

## 5. Side-by-side, honest

| Line | Managed Agents | Droplet + Gemini |
|------|---------------|------------------|
| Inference | £18–£45 | £0 (inside free-tier quota), rising if you bust it |
| Hosting | £0 | £0 marginal (already paying £18 for other reasons) |
| Persistence / recovery / UI backend | Managed | You build it once |
| Cloudflare tunnel to MCPs | Free tier | Same |
| **Marginal monthly cash** | **£18–£45** | **£0–£5** |
| Setup time | ~1 day | ~2–4 days |
| Steady-state ops | ~0 | ~1–2 h/month |

Cash-wise: **droplet path is clearly cheaper** given your existing baseline.

## 6. What Managed Agents actually buys you

Strip out the inflated numbers and this is the honest case:

1. **Zero persistence code to own.** Sessions, event logs, crash-resume,
   conversation history, token budgeting — all handled upstream. On the
   droplet path you write this, maintain it, and debug it when it fails
   during a client call.
2. **Claude > Gemini for tool-heavy agent work.** The quality gap on
   nuanced classification and client-facing draft writing is real. Not
   night-and-day, but meaningful for a consultancy where written
   communication is the product.
3. **No daily rate-limit ceiling.** Gemini CLI free tier has quotas; on a
   bad morning you can clip them and lose the agent for hours. Managed
   Agents doesn't throttle at that scale.
4. **Managed = one fewer thing in your ops stack.** The droplet already
   exists, but adding an always-on stateful agent loop to it adds
   maintenance weight — it's not just a static site anymore.

## 6b. Option C — Gemini 3 Pro on Google Cloud (Cloud Run + Vertex AI)

Added after the Mac mini path was live — Jon asked "can we run this on
gcloud?". Honest picture:

### Infrastructure at 45black's workload

| Line | Monthly | Why free |
|------|---------|----------|
| Cloud Run (min=0, ~2 vCPU-hours/mo) | £0 | Inside 180 vCPU-hours free tier |
| Firestore (~15k writes, 50k reads/mo) | £0 | Inside 20k writes/day, 50k reads/day free tier |
| Cloud Scheduler (2 jobs) | £0 | First 3 jobs free |
| Secret Manager (≤6 secrets) | £0 | First 6 active secret versions free |
| Artifact Registry (1 image, ~300 MB) | ~£0.01 | 0.5 GB free + £0.10/GB-month |
| Cloud Logging | £0 | First 50 GB/mo free |
| Networking egress | ~£0.01 | Mostly inside GCP |
| **Infra subtotal** | **~£0.02 / month** | |

### Inference on Vertex AI Gemini 3 Pro

Same token prices as AI Studio's paid tier (April 2026 indicative):
- ~$1.25 / M input, ~$10 / M output
- 75% discount on cached input

At the honest workload (9M input / 2M output monthly) with caching on:

- Input: 2.7 × $1.25 + 6.3 × $0.31 = $5.35
- Output: 2 × $10 = $20
- **~$25 / month ≈ £20 / month**

Without caching: **~£30 / month**.

### Important: subscription does *not* reduce this

Google One AI Premium / Google AI Pro / Workspace AI subscriptions
**cover the Gemini app surface, not Vertex AI API quota**. The
subscription stays useful (Gemini in Gmail sidebar, NotebookLM,
Gemini.app) but Vertex AI is billed through the GCP billing account
you link to the project — separate ledger, separate line item.

### Free credit

New GCP projects get **$300 of credit for 90 days** on first signup.
At this workload that covers roughly the first six months of inference
outright. After credit exhausts, Vertex AI is the only meaningful
recurring line item.

### Total gcloud path

- Month 1–6 (inside free credit): **~£0 / month**
- Steady state: **~£20–£30 / month**

Higher than the Mac-mini-plus-AI-Studio path but with real advantages:

1. No always-on box to maintain. Scale-to-zero when idle.
2. EU data residency out of the box (`europe-west1`, Belgium) —
   friendlier to pension data compliance.
3. Terraform-managed infra, redeployable from scratch.
4. IAP auth in front of the console — Google login gates access.
5. Secret Manager replaces `.env` for credentials.
6. Easier to share with a future team member without giving them
   Mac-mini access.

## 7. Revised recommendation — three-way

| Path | Monthly (steady) | Ops burden | Data residency | Professionalism |
|------|------------------|------------|----------------|-----------------|
| **A.** Managed Agents (Claude) | £18–£45 | near zero | US/EU (cloud region) | managed service |
| **B.** Droplet + Gemini (AI Studio) | £0–£5 marginal | 5–10h/mo you own | your choice (droplet region) | DIY |
| **C.** Cloud Run + Vertex AI (Gemini) | £0 for 6 mo then £20–£30 | near zero after setup | EU (`europe-west1`) | production-grade GCP |

**Choose C (Cloud Run + Vertex) if:** you want a deploy-once-forget-it
production setup with proper auth, no always-on box, and EU data
residency. Pay a modest cash premium for significant reduction in
ongoing ops work.

**Choose B (droplet + AI Studio) if:** minimising monthly cash is the
top priority and you enjoy owning the harness / don't mind occasional
rate-limit hiccups.

**Choose A (Managed Agents) if:** Claude's specific agent quality on
client-facing outbound mail justifies the premium over Gemini, and you
don't want any local persistence to maintain.

### Recommended default now: **Option C (Cloud Run + Vertex AI)**

Reasoning:

1. For pension-tech consulting, EU data residency + IAP-gated console is
   closer to what clients will expect if they ever ask "how do you
   handle this". The droplet path doesn't give you that for free.
2. £300 free credit covers ~6 months of inference — real-world testing
   is effectively free to start.
3. After free credit, £20–£30/mo is within the cost of a decent meal.
   For something that triages your inbox for 10+ hours of output a week,
   that's fine.
4. The code is dual-mode — starting on C doesn't lock you out of B. You
   can flip back by swapping two env vars and the SQLite file will still
   be there.

**Hybrid worth considering:** run Gemini on Cloud Run for triage /
classification, and call Claude (Anthropic API or Managed Agents) only
for client-facing outbound draft composition. Best quality per pound.
Keep one billing ledger for each provider.

## 8. Cost levers either way

1. **Cadence is the biggest one.** 30-min triage vs. 10-min = 3× less
   inference, indistinguishable in user experience for a solo operator.
2. **Model tiering.** Triage is Haiku-class work. Reserve Sonnet / Opus
   or Gemini-3-Pro for human-in-the-loop drafts.
3. **Caching.** System prompt + agent definition + recent inbox snapshot
   as a single cacheable block. 10× cheaper on cached reads.
4. **Off-hours and weekends off.** Not rocket science; just make sure the
   cron actually has the time-window filter (see `scripts/tick.py`).

Applied together these cut either bill by roughly 60%.

## 9. Leaving the first draft on the record

The first version of this doc is at commit 62f675a. Keeping the history
honest — an inflated "recommended" answer that happened to align with the
vendor I was scaffolding for is exactly the kind of thing you should
adversarially check, and you did.
