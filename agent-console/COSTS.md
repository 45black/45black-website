# Cost comparison: Claude Managed Agents vs. Gemini 3 Pro on a DO droplet

> All numbers are **indicative** — API prices drift, your usage shape will
> differ. Treat this as a sanity-check, not a quote. Last revised April 2026.

## 1. Workload assumption

Typical "always-on assistant" day for 45black:

| Task | Cadence | Rough tokens / run (in + out) |
|------|---------|-------------------------------|
| Email triage tick | every 10 min, 07–19 weekdays → ~72/day | 12k in + 2k out |
| Morning briefing | 1× weekday at 07:30 | 8k in + 3k out |
| Ad-hoc conversation (you talking to the console) | ~30 events/day | 6k in + 1.5k out average |
| Calendar find-time, drafts, etc. | 10/day | 5k in + 1k out |

**Daily totals (rough):**

- Input: ~72 × 12k + 8k + 30 × 6k + 10 × 5k ≈ **1.1 M tokens/day**
- Output: ~72 × 2k + 3k + 30 × 1.5k + 10 × 1k ≈ **0.20 M tokens/day**

Per month (≈ 21 working days):

- Input ≈ **23 M tokens/month**
- Output ≈ **4.2 M tokens/month**

Prompt caching on the system prompt + agent definition typically cuts the
input bill by 60–80% once the session is warm — I'll show both the naive
number and a cached number below.

## 2. Option A — Claude Managed Agents (recommended path)

Pricing shown is list per 1M tokens, current published rates (early 2026,
may drift — check the platform page before signing off):

| Model | Input $/M | Output $/M | Cached input $/M |
|-------|-----------|------------|------------------|
| Haiku 4.5 | ~$1 | ~$5 | ~$0.10 |
| Sonnet 4.6 | ~$3 | ~$15 | ~$0.30 |
| Opus 4.6 | ~$15 | ~$75 | ~$1.50 |

**Sonnet 4.6 (recommended default), naive:**

- Input: 23 × $3 = **$69**
- Output: 4.2 × $15 = **$63**
- **~$132 / month**

**Sonnet 4.6 with prompt caching active on ~70% of input:**

- Uncached input: 7 × $3 = $21
- Cached input: 16 × $0.30 = $4.80
- Output: 4.2 × $15 = $63
- **~$89 / month**

**Haiku 4.5 (for the triage tick; reserve Sonnet for human conversations):**

- If 80% of traffic (triage) uses Haiku and 20% (conversation) uses Sonnet,
  expected bill ≈ **$35–50 / month**.

**Managed Agents infrastructure surcharge:** currently **$0** on top of
token costs in the beta. No egress, no "compute minutes" line item. Storage
of session state / event history is included. This is the key number that
makes Managed Agents attractive vs. hosting your own runner.

**Cloudflare tunnel** (to let the cloud agent reach your local MCPs): **$0**
on the free tier, assuming you already own the 45black.tech zone.

**Total expected run-rate:** **£40–£110 / month** depending on model mix and
cache hit rate. Plus your existing Mac mini electricity (~£3/month at UK
rates if it was already on for other reasons — not a new cost).

## 3. Option B — Gemini 3 Pro on a DigitalOcean droplet ("openclaw-style")

You mentioned Gemini 3 Pro is included in your current Google subscription
(Google One AI Premium / Workspace AI tier). That's a material advantage
and it changes the shape of the comparison.

### What's actually bundled

The subscription gives you **Gemini 3 Pro through the Gemini app, NotebookLM,
and Workspace surfaces**. It does **not** give you free unmetered access to
the Gemini API (`generativelanguage.googleapis.com` /
`aiplatform.googleapis.com`). Any agent harness — OpenHands, LangGraph, a
custom runner, the OpenRouter-style "openclaw" image — calls the API, and
that's billed per-token separately from the subscription.

So the honest picture has two sub-cases.

### B1 — Use Gemini via the subscription surface (effectively £0 inference)

Only works if you interact with Gemini **through** Gemini.app, Workspace
add-ons, or NotebookLM. That means you don't get:

- a persistent agent loop you control
- tool calls into your own MCP servers
- scheduled / autonomous runs
- an event log you can wire into a UI

So for "professional UI for seeing and running agents that triage email on a
schedule", B1 **does not apply** — the subscription bundle isn't the right
shape for this use case, even though it's paid for. It's useful for ad-hoc
research, document summarisation, in-Gmail drafting via the Workspace
sidebar, etc. Keep it; don't count on it for the agent loop.

### B2 — Gemini 3 Pro API from a DO droplet with an open-source harness

Droplet cost (always-on, 2 vCPU / 4 GB enough for OpenHands / custom
runner): **~$24/month** (≈ £19). Add a reserved IP and backups: **~$30/month**.

Gemini 3 Pro API list pricing (indicative, drifts):

- ~$1.25 / M input, ~$10 / M output (standard tier). Context caching knocks
  ~75% off cached input.

Same 23 M in / 4.2 M out workload:

- Input: 23 × $1.25 = **$29**
- Output: 4.2 × $10 = **$42**
- With caching on ~70% of input: **~$50**
- **~$71–80 / month API**, plus **$24–30 droplet** = **~$95–110 / month**

Then add your own time:

- Harness install + keep-alive (OpenHands, Autogen, custom)
- Persistence layer (Postgres or SQLite + WAL)
- Crash recovery, log rotation, upgrade path
- Security: SSH hardening, fail2ban, unattended upgrades, TLS cert renewal

Call it **5–10 hours/month of maintenance** once settled; more during the
first month. Value that at whatever your time costs.

## 4. Side-by-side

| Line item                       | Claude Managed Agents         | Gemini 3 Pro on DO droplet |
|---------------------------------|-------------------------------|----------------------------|
| Inference                       | £70–£105 / mo                 | £55–£65 / mo               |
| Compute / hosting               | £0                            | £19–£24 / mo               |
| Persistence infra               | £0 (managed)                  | Included (you run it)      |
| Event log / session browser     | £0 (managed)                  | You build it               |
| Crash recovery                  | Managed                       | You build it               |
| MCP access to local tools       | Cloudflare tunnel, £0         | Direct localhost, £0       |
| Ops time                        | ~0 h / mo                     | ~5–10 h / mo               |
| **Cash, steady state**          | **£70–£105 / mo**             | **£75–£90 / mo**           |
| **Cash + ops at £100/h**        | **£70–£105 / mo**             | **£575–£1 090 / mo**       |

Cash-only, they're within spitting distance. Once you price in the fact
that one of you has to wear the DevOps hat for the droplet path — and that
you're a consultant whose hour has a billable opportunity cost — Managed
Agents wins comfortably for this use case.

## 5. When the droplet still wins

- You want a hard data-sovereignty story (no prompts leaving your UK-
  controlled infra). Neither cloud option satisfies that; a Mac-mini-only
  setup with a locally-hosted OSS model does, at a big capability cost.
- You already run infra for other reasons and the marginal cost of "one
  more service" on an existing droplet is tiny.
- You want to experiment with multiple models rapidly (Gemini, GPT, local
  Llama) and the harness becomes your model-selection layer.

## 6. Cost levers if you go with Managed Agents

1. **Route triage through Haiku 4.5.** Sonnet only kicks in when a human
   is in the loop. Saves 50–70% of the triage bill.
2. **Aggressive prompt caching.** Cache the system prompt + tool list +
   recent inbox state as a single block. Cached reads are ~10× cheaper.
3. **Throttle the triage cadence.** 15 min beats 10 min for email, and
   cuts 33% of ticks.
4. **Escalation ladder.** Haiku drafts a classification, only escalates
   the edge cases to Sonnet / Opus.
5. **Off-hours pause.** No ticks between 19:00 and 07:00 already — keep
   weekends off entirely unless you're expecting urgent mail.

Applied together, the sonnet bill above drops into the **£30–£60/month**
range without noticeably changing agent quality.

## 7. Recommendation

Start on **Claude Managed Agents**, Sonnet 4.6 for human-in-the-loop work,
Haiku 4.5 for autonomous ticks, with prompt caching on. Keep Gemini 3 Pro
for ad-hoc research via the Gemini app and Workspace sidebars — that's
where its bundle value actually lands.

Revisit the DO droplet only if either (a) the API bill exceeds £150/month
for two consecutive months, or (b) Anthropic starts charging a per-session
Managed Agents surcharge that changes the economics.
