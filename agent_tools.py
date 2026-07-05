"""
agent_tools.py

The capability layer for the agentic assistant.

Each tool is a small, single-purpose function over the CSV data, wrapped with:
  - a JSON-Schema description the LLM reads to decide *when* to call it, and
  - an RBAC scope that is enforced in `authorize()` BEFORE the tool runs.

The LLM may *request* any tool; it can only *execute* one that `authorize()`
lets through for the current user. Security is code, never the model's choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from csv_db import (
    attendance_summary,
    backlog_summary,
    contact_summary,
    find_student,
    load_students,
    mentor_summary,
    student_count,
    students_for_faculty,
)
from rbac import RoleIdentity


# ── Tool definition ──────────────────────────────────────────────────────────

Handler = Callable[[RoleIdentity, Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]          # JSON Schema for the arguments
    scope: str                          # "individual" | "aggregate" | "directory"
    handler: Handler


# ── Small, token-friendly projections ────────────────────────────────────────

def _brief(student: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "usn": student["usn"],
        "name": student["name"],
        "semester": student["semester"],
        "department": student["department"],
        "cgpa": student["cgpa"],
        "attendance_percent": student["attendance_percent"],
        "backlog_count": student["backlog_count"],
    }


def _target(eff: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the (already-authorized) target student from effective args."""
    student = find_student(eff.get("user_id", ""))
    if not student:
        raise ValueError(f"Student '{eff.get('user_id')}' not found in the CSV.")
    return student


# ── Handlers ─────────────────────────────────────────────────────────────────

def _h_profile(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    s = _target(eff)
    return {
        **_brief(s),
        "email": s["email"],
        "phone": s["phone"],
        "course_count": s["course_count"],
        "mentor": s["mentor"]["name"],
    }


def _h_attendance(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    return attendance_summary(_target(eff))


def _h_grades(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    s = _target(eff)
    return {"usn": s["usn"], "name": s["name"], "cgpa": s["cgpa"],
            "semester": s["semester"], "department": s["department"]}


def _h_backlogs(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    return backlog_summary(_target(eff))


def _h_mentor(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    return mentor_summary(_target(eff))


def _h_courses(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    s = _target(eff)
    return {"usn": s["usn"], "name": s["name"],
            "courses": [c["fullname"] for c in s["enrolled_courses"]]}


def _h_contact(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    return contact_summary(_target(eff))


def _h_student_count(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    return {"count": student_count(), "source": "students.csv"}


def _h_class_average(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    metric = (eff.get("metric") or "attendance").lower()
    field = "attendance_percent" if metric.startswith("att") else "cgpa"
    dept = (eff.get("department") or "").strip().lower()
    semester = eff.get("semester")

    cohort = [
        s for s in load_students()
        if (not dept or dept in s["department"].lower())
        and (semester in (None, "", 0) or int(s["semester"]) == int(semester))
    ]
    if not cohort:
        return {"error": "no_matching_cohort", "metric": metric,
                "department": eff.get("department"), "semester": semester}
    avg = round(sum(s[field] for s in cohort) / len(cohort), 2)
    return {"metric": metric, "average": avg, "students_counted": len(cohort),
            "department": eff.get("department") or "all", "semester": semester or "all"}


def _h_find_student(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    s = find_student(eff.get("name", ""))
    return _brief(s) if s else {"error": "not_found", "name": eff.get("name")}


def _h_list_my_students(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    if identity.role == "faculty":
        cohort = students_for_faculty(identity.user_id)
    else:  # admin
        cohort = load_students()
    return {"count": len(cohort), "students": [_brief(s) for s in cohort[:30]]}


def _h_recall_memory(identity: RoleIdentity, eff: Dict[str, Any]) -> Dict[str, Any]:
    """Query the CURRENT user's episodic memory — the agent's window into what
    was discussed in earlier turns (Level 5)."""
    from memory_store import recall
    hits = recall(identity.user_id, eff.get("query", ""), k=3)
    return {"recalled": [{"when": h["ts"], "you_asked": h["query"],
                          "we_answered": h["answer"]} for h in hits]}


# ── The registry ─────────────────────────────────────────────────────────────

_INDIVIDUAL_ARG = {
    "type": "object",
    "properties": {
        "user_id": {
            "type": "string",
            "description": "USN of the student. Students may omit this — it "
                           "defaults to their own record.",
        }
    },
}

TOOLS: List[Tool] = [
    Tool("get_profile", "Get a student's profile: department, semester, CGPA, "
         "attendance, backlog count, mentor and contact.", _INDIVIDUAL_ARG,
         "individual", _h_profile),
    Tool("get_attendance", "Get a student's attendance percentage and session "
         "breakdown.", _INDIVIDUAL_ARG, "individual", _h_attendance),
    Tool("get_grades", "Get a student's CGPA and academic standing.",
         _INDIVIDUAL_ARG, "individual", _h_grades),
    Tool("get_backlogs", "Get a student's backlog count and backlog subjects.",
         _INDIVIDUAL_ARG, "individual", _h_backlogs),
    Tool("get_mentor", "Get a student's assigned mentor / class teacher and "
         "their contact.", _INDIVIDUAL_ARG, "individual", _h_mentor),
    Tool("get_courses", "Get the list of courses a student is enrolled in.",
         _INDIVIDUAL_ARG, "individual", _h_courses),
    Tool("get_contact", "Get a student's email and phone number.",
         _INDIVIDUAL_ARG, "individual", _h_contact),

    Tool("get_student_count", "Count of all students in the database.",
         {"type": "object", "properties": {}}, "aggregate", _h_student_count),
    Tool("get_class_average", "Compute a class/cohort average for a metric "
         "('attendance' or 'cgpa'), optionally filtered by department and "
         "semester. Returns an anonymized aggregate only.",
         {"type": "object", "properties": {
             "metric": {"type": "string", "enum": ["attendance", "cgpa"]},
             "department": {"type": "string"},
             "semester": {"type": "integer"},
         }, "required": ["metric"]}, "aggregate", _h_class_average),

    Tool("find_student_by_name", "Look up a student's USN and summary by name. "
         "Staff only.", {"type": "object", "properties": {
             "name": {"type": "string"}}, "required": ["name"]},
         "directory", _h_find_student),
    Tool("list_my_students", "List the students a faculty mentors (or all "
         "students, for admin).", {"type": "object", "properties": {}},
         "directory", _h_list_my_students),

    Tool("recall_memory", "Search this conversation's earlier turns to remember "
         "what the user asked or was told before. Use when the user refers to "
         "something 'earlier' or 'again'.",
         {"type": "object", "properties": {
             "query": {"type": "string", "description": "What to look for in past turns."}
         }, "required": ["query"]}, "aggregate", _h_recall_memory),
]

TOOLS_BY_NAME: Dict[str, Tool] = {t.name: t for t in TOOLS}

# Tool groups used by the multi-agent orchestrator to give each worker a
# restricted, purpose-built toolset (Level 3).
INDIVIDUAL_TOOLS = [t.name for t in TOOLS if t.scope == "individual"]
AGGREGATE_TOOLS = [t.name for t in TOOLS if t.scope == "aggregate"]
DIRECTORY_TOOLS = [t.name for t in TOOLS if t.scope == "directory"]


def groq_tool_specs(allowed: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Render the registry into the tool-calling schema Groq/OpenAI expects.

    Pass `allowed` (a set/list of tool names) to expose only a subset — this is
    how each worker agent gets a restricted capability surface.
    """
    names = set(allowed) if allowed is not None else None
    return [
        {"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in TOOLS
        if names is None or t.name in names
    ]


# ── The RBAC gate (runs BEFORE any tool executes) ────────────────────────────

def authorize(
    identity: RoleIdentity, tool: Tool, args: Dict[str, Any]
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Decide whether `identity` may run `tool` with `args`.

    Returns (allowed, reason, effective_args). For individual-scope tools the
    effective args are normalized so a student can only ever hit their own USN
    and a faculty only their own mentees — defense in depth, not trust.
    """
    if identity.role == "unknown":
        return False, "unrecognized user — no data access", args

    if tool.scope == "aggregate":
        return True, "aggregate/anonymized data is open to all roles", args

    if tool.scope == "directory":
        if identity.role in {"faculty", "admin"}:
            return True, f"{identity.role} may use the directory", args
        return False, "students cannot look up other students", args

    if tool.scope == "individual":
        requested = (args.get("user_id") or "").strip()

        if identity.role == "student":
            if requested and find_student(requested) and \
                    find_student(requested)["usn"] != identity.user_id:
                return False, "students may only access their OWN records", args
            args["user_id"] = identity.user_id          # force self
            return True, "student self-access", args

        if identity.role == "faculty":
            target = find_student(requested) if requested else None
            if not target:
                return False, "specify a valid student USN", args
            mentees = {s["usn"] for s in students_for_faculty(identity.user_id)}
            if target["usn"] not in mentees:
                return False, "faculty may only access their own mentees", args
            args["user_id"] = target["usn"]
            return True, "faculty mentee-access", args

        if identity.role == "admin":
            if not requested:
                return False, "specify a student USN", args
            args["user_id"] = requested
            return True, "admin access", args

    return False, "no matching access rule", args


def execute_tool(
    identity: RoleIdentity, name: str, args: Dict[str, Any]
) -> Dict[str, Any]:
    """Authorize then run a tool. Every failure is returned as data the model
    can read and react to — the loop never crashes on a bad/blocked call."""
    tool = TOOLS_BY_NAME.get(name)
    if not tool:
        return {"error": "unknown_tool", "name": name}

    allowed, reason, eff = authorize(identity, tool, dict(args or {}))
    if not allowed:
        return {"error": "ACCESS_DENIED", "reason": reason}

    try:
        return tool.handler(identity, eff)
    except Exception as exc:  # surfaced to the model as an observation
        return {"error": "tool_failed", "detail": str(exc)}
