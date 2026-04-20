"""45black-tools MCP server — bespoke business tools.

A minimal FastMCP server that runs as a Cloud Run sibling alongside the
agent console. Implements the MCP Streamable HTTP transport.

Tools here are specific to 45black operations. Replace/extend as needed.

Runs on Cloud Run at $PORT, gated by bearer token from MCP_GATEWAY_TOKEN.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from fastmcp import FastMCP

mcp = FastMCP(
    "45black-tools",
    description="Bespoke 45black business tools: CRM, invoicing, GitHub.",
)

GATEWAY_TOKEN = os.environ.get("MCP_GATEWAY_TOKEN", "")


# --- CRM ------------------------------------------------------------------


# Stub CRM — replace with real data source (Airtable, Notion, Sheets, etc.)
CRM: list[dict] = [
    {
        "id": "c001",
        "name": "Pension Corp Ltd",
        "contact": "Sarah Chen",
        "email": "sarah@pensioncorp.example",
        "status": "active",
        "last_contact": "2026-04-15",
        "notes": "Apex Governance rollout Q2 2026",
    },
    {
        "id": "c002",
        "name": "Meridian Trustees",
        "contact": "James Okafor",
        "email": "james@meridian-trustees.example",
        "status": "prospect",
        "last_contact": "2026-04-10",
        "notes": "Interested in Conveyancing Companion demo",
    },
]


@mcp.tool()
def crm_search(query: str) -> str:
    """Search the 45black CRM by name, contact, or status. Returns matching records."""
    q = query.lower()
    hits = [
        c for c in CRM
        if q in c["name"].lower()
        or q in c["contact"].lower()
        or q in c["status"].lower()
        or q in c.get("notes", "").lower()
    ]
    if not hits:
        return json.dumps({"results": [], "message": f"No CRM records matching '{query}'"})
    return json.dumps({"results": hits, "count": len(hits)})


@mcp.tool()
def crm_get(client_id: str) -> str:
    """Get a single CRM record by client ID."""
    for c in CRM:
        if c["id"] == client_id:
            return json.dumps(c)
    return json.dumps({"error": f"No client with id {client_id}"})


# --- Invoicing -------------------------------------------------------------


@mcp.tool()
def invoice_skeleton(
    client_name: str,
    description: str,
    amount_gbp: float,
    due_days: int = 30,
) -> str:
    """Generate a skeleton invoice JSON for review. Does NOT send — returns draft data."""
    now = datetime.now(timezone.utc)
    return json.dumps({
        "invoice_number": f"45B-{now.strftime('%Y%m%d')}-DRAFT",
        "client": client_name,
        "description": description,
        "amount": f"£{amount_gbp:,.2f}",
        "currency": "GBP",
        "vat_rate": "20%",
        "vat": f"£{amount_gbp * 0.2:,.2f}",
        "total": f"£{amount_gbp * 1.2:,.2f}",
        "issued": now.strftime("%Y-%m-%d"),
        "due_days": due_days,
        "status": "DRAFT — review before sending",
    })


# --- GitHub ----------------------------------------------------------------


@mcp.tool()
def github_list_recent_issues(repo: str = "45black/45black-website", limit: int = 10) -> str:
    """List recent open issues for a 45black GitHub repo."""
    import httpx

    gh_token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/issues",
            headers=headers,
            params={"state": "open", "per_page": limit, "sort": "updated"},
            timeout=15.0,
        )
        resp.raise_for_status()
        issues = [
            {
                "number": i["number"],
                "title": i["title"],
                "author": i["user"]["login"],
                "labels": [l["name"] for l in i.get("labels", [])],
                "updated": i["updated_at"],
                "url": i["html_url"],
            }
            for i in resp.json()
            if "pull_request" not in i
        ]
        return json.dumps({"issues": issues, "count": len(issues)})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# --- Server entry point ----------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    mcp.run(transport="http", host="0.0.0.0", port=port)
