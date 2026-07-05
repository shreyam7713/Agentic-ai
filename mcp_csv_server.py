"""
mcp_csv_server.py

MCP-style tool layer for the CSV student database.
The app can call these named tools internally, and FastAPI exposes them via
/mcp/tools and /mcp/call for inspection/testing.
"""

from __future__ import annotations

from typing import Any, Dict

from data_retriever import get_user_context, retrieve_data


TOOLS = [
    {
        "name": "get_user_context",
        "description": "Load student/faculty context from students_500.csv.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "role": {"type": "string"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "retrieve_data",
        "description": "Retrieve CSV data for a classified academic intent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "entity": {"type": "string"},
                "role": {"type": "string"},
                "user_id": {"type": "string"},
            },
            "required": ["intent", "user_id"],
        },
    },
]


def list_tools() -> list[Dict[str, Any]]:
    return TOOLS


def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "get_user_context":
        return get_user_context(
            user_id=arguments.get("user_id", ""),
            role=arguments.get("role", "student"),
        )

    if name == "retrieve_data":
        return retrieve_data(
            intent=arguments.get("intent", "general"),
            entity=arguments.get("entity", "general"),
            role=arguments.get("role", "student"),
            user_id=arguments.get("user_id", ""),
            assignments_path=arguments.get("assignments_path", ""),
        )

    raise ValueError(f"Unknown MCP tool: {name}")
