# MCP servers as Cloud Run siblings

Template for deploying MCP tool servers alongside the console on Cloud
Run. Each MCP server is its own Cloud Run service, reachable at a
private URL via Cloud Run service-to-service auth (no public ingress).

## Example: 45black-tools

The `server.py` in this directory is a minimal FastMCP server exposing
bespoke 45black business tools: CRM lookup, invoice skeleton generation,
and a GitHub poke. Replace or extend with your own.

## Deploy

```bash
cd deploy/mcp

# Build + deploy
gcloud builds submit \
    --tag europe-west1-docker.pkg.dev/${PROJECT_ID}/agents/mcp-45black-tools

gcloud run deploy mcp-45black-tools \
    --image europe-west1-docker.pkg.dev/${PROJECT_ID}/agents/mcp-45black-tools \
    --region europe-west1 \
    --no-allow-unauthenticated \
    --service-account agent-console@${PROJECT_ID}.iam.gserviceaccount.com \
    --set-env-vars=MCP_GATEWAY_TOKEN=$(gcloud secrets versions access latest --secret=mcp-gateway-token)
```

## Internal networking

The console's MCP bridge calls each MCP server's Cloud Run URL. Since
both services use `--no-allow-unauthenticated`, the console's service
account needs `roles/run.invoker` on the MCP service:

```bash
gcloud run services add-iam-policy-binding mcp-45black-tools \
    --region=europe-west1 \
    --member="serviceAccount:agent-console@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

Then update `mcp/servers.json` to point at the Cloud Run URL:

```json
{
    "name": "45black-tools",
    "url": "https://mcp-45black-tools-HASH.run.app"
}
```

The MCP bridge adds the `Authorization: Bearer` header automatically
from `MCP_GATEWAY_TOKEN`. On Cloud Run, you can also use the identity
token for service-to-service auth — but the bearer token pattern is
simpler and matches the cloudflared tunnel path.

## Adding a new MCP server

1. Copy `server.py` → `my_tools.py`, add your tools with `@mcp.tool()`.
2. Copy the `Dockerfile`, change the entrypoint.
3. Deploy as above.
4. Add the URL to `mcp/servers.json`.
5. Redeploy the console (or just restart it — MCP discovery runs on
   session creation, not on startup).
