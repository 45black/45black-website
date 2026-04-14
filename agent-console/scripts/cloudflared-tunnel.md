# Cloudflared tunnel for local MCP servers

Managed Agents run in Anthropic's cloud, so they need a public HTTPS endpoint
to reach MCP servers that hold local credentials (Gmail OAuth, your CRM, etc).
A Cloudflare tunnel is the right shape for this:

- No inbound ports on the Mac mini.
- TLS terminated by Cloudflare.
- Cloudflare Access can gate the endpoint to Anthropic's egress IPs plus your
  own email (defence in depth on top of the bearer token the MCP server
  enforces).
- Free tier covers this workload comfortably — already included if you own a
  domain on Cloudflare.

## Topology

```
┌────────────────────────┐           ┌────────────────────┐
│ Anthropic Managed      │  HTTPS    │ mcp.45black.tech   │
│  Agents (cloud)        │ ────────► │ (Cloudflare edge)  │
└────────────────────────┘           └─────────┬──────────┘
                                               │ cloudflared tunnel
                                               ▼
                                  ┌────────────────────────┐
                                  │ Mac mini (always on)   │
                                  │ ├─ 8790 google MCP     │
                                  │ ├─ 8791 45black MCP    │
                                  │ └─ 8787 console UI     │
                                  └────────────────────────┘
```

## One-off setup

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create 45black-mcp
# note the tunnel UUID it prints
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: 45black-mcp
credentials-file: /Users/jon/.cloudflared/<UUID>.json

ingress:
  - hostname: mcp.45black.tech
    path: /google-workspace/*
    service: http://localhost:8790
  - hostname: mcp.45black.tech
    path: /internal/*
    service: http://localhost:8791
  - service: http_status:404
```

Route DNS and start the tunnel as a service:

```bash
cloudflared tunnel route dns 45black-mcp mcp.45black.tech
sudo cloudflared service install
```

## Locking it down

Cloudflare Zero Trust → Access → Applications → Add application:

- Type: Self-hosted
- Domain: `mcp.45black.tech`
- Policy: Service tokens only (one for Anthropic, one for your scheduler)
- Optional: IP allowlist for Anthropic's published egress ranges

Then set `MCP_GATEWAY_TOKEN` in `.env` to the service token Cloudflare issues.
Every request from Managed Agents carries both the Access service token
(checked at the edge) and the bearer token (checked by the MCP server itself).

## Why this beats a DO droplet

A Cloudflare tunnel replaces the "public VPS with an open port" pattern
entirely. You keep the code and credentials on your Mac mini, pay nothing
extra, and get DDoS protection + identity-aware access for free. The only
thing you sacrifice is uptime when the Mac mini is offline — which for a
single-operator consultancy is a bearable risk.
