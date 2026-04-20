"""MCP bridge — translates between Gemini function calling and MCP servers.

Each MCP server in ``mcp/servers.json`` speaks JSON-RPC over HTTP (the MCP
Streamable HTTP transport). This module:

1. Discovers available tools from each server at startup (``tools/list``).
2. Converts MCP tool schemas to Gemini ``FunctionDeclaration`` objects.
3. Dispatches Gemini ``FunctionCall`` responses to the right server via
   MCP ``tools/call``.
4. Converts MCP results back to Gemini ``FunctionResponse`` dicts.

All calls go over HTTPS to the Cloudflare tunnel endpoint, which forwards
to the Mac mini's localhost MCP servers. Auth is a bearer token from
``MCP_GATEWAY_TOKEN``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)


def _load_mcp_config() -> list[dict[str, Any]]:
    if not settings.mcp_config_path.exists():
        return []
    data = json.loads(settings.mcp_config_path.read_text())
    return data.get("servers", [])


def _bearer_token(server: dict[str, Any]) -> str | None:
    auth = server.get("auth", {})
    if auth.get("type") != "bearer":
        return None
    env_key = auth.get("token_env", "")
    return os.environ.get(env_key) if env_key else None


def _server_url(server: dict[str, Any]) -> str:
    """Pick tunnel URL or fall back to local dev URL."""
    if os.environ.get("MCP_USE_LOCAL"):
        return server.get("_local_dev_url", server["url"])
    return server["url"]


class MCPBridge:
    """Discovers and calls MCP tools across all configured servers."""

    def __init__(self) -> None:
        self._servers = _load_mcp_config()
        self._client = httpx.Client(timeout=30.0)
        # tool_name → server config (populated by discover())
        self._tool_map: dict[str, dict[str, Any]] = {}
        # tool_name → MCP schema (populated by discover())
        self._tool_schemas: dict[str, dict[str, Any]] = {}

    def discover(self) -> list[dict[str, Any]]:
        """Call tools/list on every server. Returns Gemini FunctionDeclaration dicts."""
        declarations: list[dict[str, Any]] = []
        for server in self._servers:
            url = _server_url(server)
            token = _bearer_token(server)
            headers: dict[str, str] = {"content-type": "application/json"}
            if token:
                headers["authorization"] = f"Bearer {token}"

            allowlist = set(server.get("tools_allowlist", []))

            try:
                resp = self._client.post(
                    url,
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                )
                resp.raise_for_status()
                tools = resp.json().get("result", {}).get("tools", [])
            except Exception as exc:
                log.warning("MCP discover failed for %s: %s", server.get("name"), exc)
                continue

            for tool in tools:
                name = tool["name"]
                if allowlist and name not in allowlist:
                    continue
                self._tool_map[name] = server
                self._tool_schemas[name] = tool
                declarations.append(self._to_gemini_declaration(tool))
                log.info("MCP tool: %s from %s", name, server.get("name"))

        return declarations

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute an MCP tool call. Returns the result payload."""
        server = self._tool_map.get(name)
        if not server:
            return {"error": f"unknown tool: {name}"}

        url = _server_url(server)
        token = _bearer_token(server)
        headers: dict[str, str] = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": 2,
        }

        try:
            resp = self._client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                return {"error": body["error"]}
            result = body.get("result", {})
            # MCP returns content array; flatten text parts for Gemini.
            content_parts = result.get("content", [])
            text_parts = [p.get("text", "") for p in content_parts if p.get("type") == "text"]
            return {"result": "\n".join(text_parts) if text_parts else json.dumps(result)}
        except Exception as exc:
            return {"error": str(exc)}

    @staticmethod
    def _to_gemini_declaration(mcp_tool: dict[str, Any]) -> dict[str, Any]:
        """Convert an MCP tool schema to a Gemini FunctionDeclaration dict.

        MCP uses JSON Schema for ``inputSchema``; Gemini uses a subset of
        OpenAPI Schema which is structurally very similar. We pass it through
        and let the SDK coerce minor differences.
        """
        input_schema = mcp_tool.get("inputSchema", {})
        params = {
            k: v
            for k, v in input_schema.items()
            if k in ("type", "properties", "required", "description")
        }
        return {
            "name": mcp_tool["name"],
            "description": mcp_tool.get("description", ""),
            "parameters": params or {"type": "object", "properties": {}},
        }

    @property
    def tool_names(self) -> list[str]:
        return list(self._tool_map.keys())
