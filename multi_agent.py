"""
multi_agent.py  (Level 3 — planner + workers + synthesizer over a blackboard)

A genuine multi-agent system, not a single loop:

    Planner ──▶ RBAC-Guard ──▶ [ Data worker | Analytics worker ] ──▶ Synthesizer
       │            │                    │                                 │
       └──── all read & write the shared Blackboard (data + trace) ────────┘
                              then: reflection critic → output guardrail

Each agent has a distinct responsibility and a *restricted* capability surface:
  - Planner       : an LLM, NO data tools — only decomposes the goal into subtasks.
  - RBAC-Guard    : validates each planned access against the user's role BEFORE
                    any worker runs, and drops out-of-scope subtasks (policy by
                    reasoning + code, logged in the trace).
  - Data worker   : individual-scope tools only (a student's own record).
  - Analytics     : aggregate/directory tools only (counts, averages, rosters).
  - Synthesizer   : an LLM, NO tools — merges worker results into the answer.

Communication is real: the planner's structured output is the message the
workers consume, and the workers' results are messages the synthesizer consumes,
all recorded on the Blackboard — the explainability layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from agent_loop import run_agent
from agent_tools import AGGREGATE_TOOLS, DIRECTORY_TOOLS, INDIVIDUAL_TOOLS
from guardrails import scan_answer
from reflection import critique
from rbac import RoleIdentity, resolve_identity

MODEL = "llama-3.3-70b-versatile"
MAX_SUBTASKS = 3


# ── Shared blackboard ────────────────────────────────────────────────────────

@dataclass
class Blackboard:
    """Shared state every agent reads and writes."""
    query: str
    identity: RoleIdentity
    plan: List[Dict[str, str]] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)   # worker outputs
    observations: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[str] = field(default_factory=list)
    trace: List[Dict[str, str]] = field(default_factory=list)

    def note(self, agent: str, action: str, status: str, detail: str) -> None:
        self.trace.append({"agent": agent, "action": action,
                           "status": status, "detail": detail})


# ── Planner ──────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = (
    "You are the PLANNER of a multi-agent academic assistant. Decompose the "
    "user's question into 1-3 concrete subtasks and assign each to a worker:\n"
    "  - 'data'      : facts about a single student's own record (profile, "
    "attendance, grades, backlogs, mentor, courses, contact).\n"
    "  - 'analytics' : counts, class/cohort averages, or listing students "
    "(directory).\n"
    "Respond with ONLY JSON: {\"subtasks\": [{\"agent\": \"data|analytics\", "
    "\"task\": \"...\"}]}. Keep it minimal — do not invent work the user "
    "didn't ask for."
)


def _extract_json(text: str) -> Dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _plan(client: Any, bb: Blackboard, model: str) -> List[Dict[str, str]]:
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0.0,
            messages=[{"role": "system", "content": _PLANNER_SYSTEM},
                      {"role": "user", "content": bb.query}],
        )
        parsed = _extract_json(resp.choices[0].message.content or "")
    except Exception as exc:
        bb.note("planner", "plan", "error", f"Planner failed ({exc}); using default plan.")
        parsed = None

    subtasks: List[Dict[str, str]] = []
    if parsed and isinstance(parsed.get("subtasks"), list):
        for st in parsed["subtasks"][:MAX_SUBTASKS]:
            agent = "analytics" if str(st.get("agent")).lower().startswith("anal") else "data"
            task = str(st.get("task") or "").strip()
            if task:
                subtasks.append({"agent": agent, "task": task})

    if not subtasks:  # robust fallback: treat the whole query as one data task
        subtasks = [{"agent": "data", "task": bb.query}]

    bb.note("planner", "decompose", "completed",
            f"Planned {len(subtasks)} subtask(s): "
            + "; ".join(f"[{s['agent']}] {s['task']}" for s in subtasks))
    return subtasks


# ── RBAC guard ───────────────────────────────────────────────────────────────

_DIRECTORY_HINTS = ("all students", "every student", "list of students",
                    "other student", "everyone", "class list", "roster",
                    "browse", "each student")


def _intended_scope(task: str) -> str:
    low = task.lower()
    if any(h in low for h in _DIRECTORY_HINTS):
        return "directory"
    if any(h in low for h in ("average", "how many", "count", "total", "statistic", "aggregate")):
        return "aggregate"
    return "individual"


def _guard(bb: Blackboard) -> List[Dict[str, str]]:
    """Validate each planned subtask against the caller's role BEFORE workers run.
    Students have no directory access at all, so any subtask that — on its own OR
    in light of the original question — implies browsing other students is
    dropped and logged. (The per-tool RBAC gate is the hard backstop.)"""
    query_scope = _intended_scope(bb.query)
    allowed: List[Dict[str, str]] = []
    for st in bb.plan:
        scope = st_scope = _intended_scope(st["task"])
        if bb.identity.role == "student" and "directory" in (st_scope, query_scope):
            scope = "directory"
        if scope == "directory" and bb.identity.role == "student":
            bb.note("rbac-guard", "block", "denied",
                    f"Blocked '{st['task']}' — students cannot browse other students.")
            continue
        bb.note("rbac-guard", "authorize", "completed",
                f"Allowed [{st['agent']}] '{st['task']}' (scope={scope}).")
        allowed.append(st)
    return allowed


# ── Workers ──────────────────────────────────────────────────────────────────

_WORKER_PROMPTS = {
    "data": (
        "You are the DATA worker. Use ONLY your tools to fetch the requesting "
        "user's own academic record and answer the subtask concisely with real "
        "values. The user's role is '{role}', id '{user_id}'. Never guess a number."
    ),
    "analytics": (
        "You are the ANALYTICS worker. Use ONLY your tools to compute counts, "
        "class/cohort averages, or student listings for the subtask. The user's "
        "role is '{role}', id '{user_id}'. Report anonymized aggregates and base "
        "every number on tool results."
    ),
}

_WORKER_TOOLS = {
    "data": INDIVIDUAL_TOOLS + ["recall_memory"],
    "analytics": AGGREGATE_TOOLS + DIRECTORY_TOOLS,
}


def _run_worker(client: Any, bb: Blackboard, subtask: Dict[str, str], model: str) -> None:
    agent = subtask["agent"]
    prompt = _WORKER_PROMPTS[agent].format(role=bb.identity.role, user_id=bb.identity.user_id)
    result = run_agent(
        client=client,
        user_id=bb.identity.user_id,
        query=subtask["task"],
        model=model,
        max_steps=5,
        identity=bb.identity,
        allowed_tools=_WORKER_TOOLS[agent],
        system_prompt=prompt,
        agent_name=f"{agent}-worker",
    )
    bb.findings.append({"agent": agent, "task": subtask["task"], "answer": result["answer"]})
    bb.observations.extend(result.get("observations", []))
    bb.tool_calls.extend(result.get("tool_calls", []))
    # Fold the worker's own decision trace onto the blackboard (skip its rbac line).
    for step in result.get("trace", []):
        if step.get("action") != "resolve_identity":
            bb.trace.append(step)
    bb.note(f"{agent}-worker", "report", "completed",
            f"Answered subtask with {len(result.get('tool_calls', []))} tool call(s).")


# ── Synthesizer ──────────────────────────────────────────────────────────────

_SYNTH_SYSTEM = (
    "You are the SYNTHESIZER of a multi-agent assistant. You have NO tools. "
    "Combine the worker findings below into one clear, direct answer to the "
    "user's question. Use only the facts in the findings; do not invent data. "
    "If the findings say access was denied, explain that politely."
)


def _synthesize(client: Any, bb: Blackboard, model: str) -> str:
    findings_blob = json.dumps(bb.findings, default=str)[:6000]
    user = (f"USER QUESTION:\n{bb.query}\n\nWORKER FINDINGS:\n{findings_blob}\n\n"
            "Write the final answer.")
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0.2,
            messages=[{"role": "system", "content": _SYNTH_SYSTEM},
                      {"role": "user", "content": user}],
        )
        answer = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        answer = bb.findings[0]["answer"] if bb.findings else f"Could not synthesize an answer ({exc})."
    bb.note("synthesizer", "compose", "completed",
            f"Merged {len(bb.findings)} finding(s) into the final answer.")
    return answer


# ── Orchestrator ─────────────────────────────────────────────────────────────

def run_multi_agent(
    *,
    client: Any,
    user_id: str,
    query: str,
    model: str = MODEL,
    reflect: bool = True,
    guard_output: bool = True,
) -> Dict[str, Any]:
    """Run the full planner→workers→synthesizer pipeline and return a rich,
    explainable result dict."""
    identity = resolve_identity(user_id)
    bb = Blackboard(query=query, identity=identity)
    bb.note("rbac-guard", "resolve_identity", "completed",
            f"Resolved role='{identity.role}' for id='{identity.user_id}'.")

    if identity.role == "unknown":
        bb.note("orchestrator", "halt", "denied", "Unrecognized user — no data access.")
        return _package(bb, "I could not recognize your ID, so I can't share any "
                            "records. Please check your student USN or staff ID.",
                        reflection=None, guardrail=None)

    # 1) Plan  2) Guard  3) Dispatch workers  4) Synthesize
    bb.plan = _plan(client, bb, model)
    approved = _guard(bb)
    for subtask in approved:
        _run_worker(client, bb, subtask, model)

    if not bb.findings:
        answer = "I wasn't able to gather the data needed to answer that within your access level."
        return _package(bb, answer, reflection=None, guardrail=None)

    answer = _synthesize(client, bb, model)

    # 5) Reflection critic (self-correction against the observed data)
    reflection_out = None
    if reflect:
        reflection_out = critique(client, query=query, observations=bb.observations,
                                  draft_answer=answer, model=model)
        answer = reflection_out["answer"]
        status = "completed" if reflection_out.get("supported", True) else "revised"
        bb.note("critic", "reflect", status,
                "Answer supported by data." if reflection_out.get("supported", True)
                else f"Corrected unsupported claims: {reflection_out.get('issues')}")

    # 6) Output guardrail (no leaking another person's PII)
    guardrail_out = None
    if guard_output:
        guardrail_out = scan_answer(identity, answer)
        if not guardrail_out["safe"]:
            answer = guardrail_out["redacted_answer"]
            bb.note("guardrail", "redact", "revised",
                    f"Redacted disallowed identifiers: {guardrail_out['violations']}")
        else:
            bb.note("guardrail", "scan", "completed", "No data-leak violations found.")

    return _package(bb, answer, reflection=reflection_out, guardrail=guardrail_out)


def _package(bb: Blackboard, answer: str, *, reflection, guardrail) -> Dict[str, Any]:
    return {
        "answer": answer,
        "role": bb.identity.role,
        "plan": bb.plan,
        "findings": bb.findings,
        "tool_calls": bb.tool_calls,
        "observations": bb.observations,
        "trace": bb.trace,
        "reflection": reflection,
        "guardrail": guardrail,
    }
