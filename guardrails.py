"""
guardrails.py  (Level 5 — output guardrail)

A final, deterministic check that an answer does not leak another person's
personal data before it is returned to the user.

RBAC (agent_tools.authorize) already prevents a student from *fetching* another
student's record. This is defense-in-depth on the *output* side: even if the
model paraphrases or a tool over-returns, we scan the text for USNs, emails and
phone numbers that are not part of the data this user is allowed to see, and
redact them. Every action is logged in the trace, so the guardrail is
explainable rather than a silent filter.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from csv_db import load_students, students_for_faculty
from rbac import RoleIdentity

USN_RE = re.compile(r"\b[0-9][A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{3}\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b[6-9]\d{9}\b")


def _allowed_identifiers(identity: RoleIdentity) -> Dict[str, set]:
    """The USNs / emails / phones this identity is permitted to see in output."""
    usns: set[str] = set()
    emails: set[str] = set()
    phones: set[str] = set()

    def _add(student: Dict[str, Any]) -> None:
        usns.add(student["usn"].upper())
        for key in ("email", "college_email", "personal_email"):
            if student.get(key):
                emails.add(student[key].lower())
        if student.get("phone"):
            phones.add(student["phone"])
        # A student's own mentor/class-teacher contacts are legitimately theirs.
        for staff in (student.get("mentor") or {}, student.get("class_teacher") or {}):
            if staff.get("email"):
                emails.add(staff["email"].lower())
            if staff.get("phone"):
                phones.add(staff["phone"])

    if identity.role == "admin":
        for s in load_students():
            _add(s)
    elif identity.role == "faculty":
        for s in students_for_faculty(identity.user_id):
            _add(s)
    elif identity.role == "student":
        for s in load_students():
            if s["usn"].upper() == identity.user_id.upper():
                _add(s)
                break
    return {"usns": usns, "emails": emails, "phones": phones}


def scan_answer(identity: RoleIdentity, answer: str) -> Dict[str, Any]:
    """Return {safe, violations, redacted_answer}. Redacts any identifier that
    is not in the caller's allowed set."""
    allowed = _allowed_identifiers(identity)
    violations: List[str] = []
    redacted = answer or ""

    for label, pattern, key in (
        ("USN", USN_RE, "usns"),
        ("email", EMAIL_RE, "emails"),
        ("phone", PHONE_RE, "phones"),
    ):
        for match in pattern.findall(redacted):
            token = match.upper() if key == "usns" else (match.lower() if key == "emails" else match)
            if token not in allowed[key]:
                violations.append(f"{label}:{match}")
                redacted = redacted.replace(match, f"[redacted-{label}]")

    return {
        "safe": not violations,
        "violations": violations,
        "redacted_answer": redacted,
    }
