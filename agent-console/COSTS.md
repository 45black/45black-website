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

## 7. Revised recommendation

**Start on the droplet path, not Managed Agents.**

You're already paying for the droplet and the subs. Build/install a
harness (OpenHands, Letta, or a slim custom FastAPI + Gemini CLI loop)
alongside whatever the droplet already runs. Point it at Gemini via CLI
auth, use the Cloudflare tunnel to reach the Mac mini's MCPs. Marginal
cost ~£0/month.

The console frontend + MCP config + agent definitions + launchd wiring
from this scaffold all port across unchanged — only the backend LLM
adapter changes. A `backend/gemini_harness.py` sketch is in this repo.

**Move to Managed Agents later if:**

- Gemini quota trips more than a couple of times a month (rate limits
  during actual work hurt more than a small cash cost).
- You find yourself spending weekends on harness maintenance rather than
  on 45black work — at that point Managed Agents' £18–£45/month is worth
  it to offload state and recovery.
- You want Claude's specific draft-writing quality on the client-facing
  outbound mail, in which case consider a **hybrid**: Gemini on the droplet
  for triage/classification, Managed Agents (or raw Anthropic API) called
  selectively from the droplet when a human-quality draft is needed.

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
