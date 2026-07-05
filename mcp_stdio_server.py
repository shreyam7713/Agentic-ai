"""
mcp_stdio_server.py  (Level 4 — a REAL Model Context Protocol server)

Unlike mcp_csv_server.py (in-process function dispatch), this is a genuine MCP
server built on the official `mcp` SDK. It exposes the CSV capability layer as
MCP tools over a stdio transport, so any MCP client — including this app's
mcp_client.py — discovers and calls them across a real process + protocol
boundary.

Every tool resolves the caller's identity and runs through the SAME RBAC gate
(agent_tools.execute_tool), so access control holds over the protocol too.

Run standalone:      python mcp_stdio_server.py
Inspect (optional):  npx @modelcontextprotocol/inspector python mcp_stdio_server.py
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from agent_tools import execute_tool
from rbac import resolve_identity

mcp = FastMCP("moodle-csv")


def _run(name: str, user_id: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve identity from the caller-supplied id, then execute through RBAC."""
    identity = resolve_identity(user_id)
    return execute_tool(identity, name, args or {})


# ── Individual-scope tools (a student's own record; RBAC forces self-access) ──

@mcp.tool()
def get_profile(user_id: str) -> dict:
    """Student profile: department, semester, CGPA, attendance, backlog count, mentor, contact."""
    return _run("get_profile", user_id)


@mcp.tool()
def get_attendance(user_id: str) -> dict:
    """A student's attendance percentage and session breakdown."""
    return _run("get_attendance", user_id)


@mcp.tool()
def get_grades(user_id: str) -> dict:
    """A student's CGPA and academic standing."""
    return _run("get_grades", user_id)


@mcp.tool()
def get_backlogs(user_id: str) -> dict:
    """A student's backlog count and backlog subjects."""
    return _run("get_backlogs", user_id)


@mcp.tool()
def get_mentor(user_id: str) -> dict:
    """A student's assigned mentor / class teacher and their contact."""
    return _run("get_mentor", user_id)


@mcp.tool()
def get_courses(user_id: str) -> dict:
    """The list of courses a student is enrolled in."""
    return _run("get_courses", user_id)


@mcp.tool()
def get_contact(user_id: str) -> dict:
    """A student's email and phone number."""
    return _run("get_contact", user_id)


# ── Aggregate-scope tools (anonymized; open to any recognized role) ───────────

@mcp.tool()
def get_student_count(user_id: str) -> dict:
    """Count of all students in the database. `user_id` identifies the caller."""
    return _run("get_student_count", user_id)


@mcp.tool()
def get_class_average(user_id: str, metric: str = "attendance",
                      department: str = "", semester: int = 0) -> dict:
    """Class/cohort average for a metric ('attendance' or 'cgpa'), optionally
    filtered by department and semester. Returns an anonymized aggregate."""
    args: Dict[str, Any] = {"metric": metric}
    if department:
        args["department"] = department
    if semester:
        args["semester"] = semester
    return _run("get_class_average", user_id, args)


# ── Directory-scope tools (faculty/admin only; RBAC enforced) ────────────────

@mcp.tool()
def find_student_by_name(user_id: str, name: str) -> dict:
    """Look up a student's USN and summary by name. Staff only."""
    return _run("find_student_by_name", user_id, {"name": name})


@mcp.tool()
def list_my_students(user_id: str) -> dict:
    """List the students a faculty mentors (or all students, for admin)."""
    return _run("list_my_students", user_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
