"""
mcp_client.py  (Level 4 — the MCP client side of the boundary)

Connects to mcp_stdio_server.py as a real MCP client: it launches the server as
a subprocess, speaks MCP over stdio, discovers the tools at runtime, and calls
them. This is what makes the "MCP integration" claim true — a genuine
process + protocol boundary, not an in-process import.

The FastAPI app uses the sync wrappers below (from a worker thread). If the MCP
subprocess can't be started for any reason, both wrappers fall back to the
in-process RBAC-gated path so the app never hard-fails on transport issues; the
returned payload records which transport actually served the call.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent
SERVER_SCRIPT = str(BASE_DIR / "mcp_stdio_server.py")


# ── Async core (a fresh session per call keeps it simple and stateless) ──────

async def _list_tools_async() -> List[Dict[str, Any]]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT],
                                   cwd=str(BASE_DIR))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.list_tools()
            return [{"name": t.name, "description": t.description,
                     "input_schema": t.inputSchema} for t in resp.tools]


async def _call_tool_async(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT],
                                   cwd=str(BASE_DIR))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments or {})
            return _unwrap(result)


def _unwrap(result: Any) -> Dict[str, Any]:
    """Pull the tool's dict payload out of an MCP CallToolResult."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps non-model returns under a single "result" key.
        if set(structured.keys()) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


# ── Sync wrappers used by FastAPI (call these via asyncio.to_thread) ─────────

def list_tools_via_mcp() -> Dict[str, Any]:
    try:
        tools = asyncio.run(_list_tools_async())
        return {"transport": "mcp-stdio", "tools": tools}
    except Exception as exc:  # transport unavailable → in-process fallback
        from agent_tools import groq_tool_specs
        tools = [{"name": t["function"]["name"],
                  "description": t["function"]["description"],
                  "input_schema": t["function"]["parameters"]}
                 for t in groq_tool_specs()]
        return {"transport": "in-process-fallback", "error": str(exc), "tools": tools}


def call_tool_via_mcp(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = asyncio.run(_call_tool_async(name, arguments))
        return {"transport": "mcp-stdio", "result": result}
    except Exception as exc:  # transport unavailable → in-process fallback
        from agent_tools import execute_tool
        from rbac import resolve_identity
        identity = resolve_identity((arguments or {}).get("user_id", ""))
        result = execute_tool(identity, name, arguments or {})
        return {"transport": "in-process-fallback", "error": str(exc), "result": result}
