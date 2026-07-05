"""
agent_loop.py

The agentic core: an LLM tool-calling loop.

Unlike the deterministic pipeline in agentic_workflow.py — where Python picks the
tool via keyword rules — here the *model* decides which tool to call, reads the
result, and decides whether to call another tool or answer. Python only:
  1. executes whatever tool the model names (through the RBAC gate), and
  2. loops, with a step budget that guarantees termination.

    decide (LLM) → authorize + execute (code) → observe (feed result back) → repeat
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from agent_tools import execute_tool, groq_tool_specs
from rbac import RoleIdentity, resolve_identity


def _step(agent: str, action: str, status: str, detail: str) -> Dict[str, str]:
    return {"agent": agent, "action": action, "status": status, "detail": detail}


def _system_prompt(identity: RoleIdentity) -> str:
    return (
        "You are Moodle AI, the academic assistant for NMIT. You answer using "
        "the provided tools, which read the real student database. Rules:\n"
        f"- The current user's role is '{identity.role}' and their id is "
        f"'{identity.user_id}'.\n"
        "- To answer anything about grades, attendance, mentors, backlogs, "
        "courses or contacts, CALL A TOOL first — never guess a value.\n"
        "- You may chain several tools (e.g. get_attendance then get_backlogs) "
        "before answering a compound question.\n"
        "- If a tool returns an ACCESS_DENIED error, do not retry it — tell the "
        "user you can only share data their role permits.\n"
        "- For a student, the individual-data tools default to their own record; "
        "you do not need to pass a user_id.\n"
        "- When you have enough information, reply with the final answer and NO "
        "tool call. Be concise and factual; base every number on tool results."
    )


def _serialize_tool_calls(tool_calls) -> List[Dict[str, Any]]:
    return [
        {"id": tc.id, "type": "function",
         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        for tc in tool_calls
    ]


def run_agent(
    *,
    client: Any,
    user_id: str,
    query: str,
    model: str = "llama-3.3-70b-versatile",
    max_steps: int = 6,
    history: List[Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    """
    Run the tool-calling agent loop for one query.

    `client` is a Groq client (chat.completions.create with tool support).
    Returns {answer, role, trace, tool_calls} where trace records every
    decision the loop made — the explainability layer.
    """
    identity = resolve_identity(user_id)
    trace: List[Dict[str, str]] = [
        _step("rbac-guard", "resolve_identity", "completed",
              f"Resolved role='{identity.role}' for id='{identity.user_id}'.")
    ]

    tools = groq_tool_specs()
    messages: List[Dict[str, Any]] = [{"role": "system", "content": _system_prompt(identity)}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": query})

    tool_calls_made: List[str] = []

    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",     # ← the model decides: tool, or finish
            temperature=0.2,
        )
        msg = resp.choices[0].message
        calls = msg.tool_calls or []

        # No tool call → the model is done reasoning.
        if not calls:
            trace.append(_step("reasoner", "final_answer", "completed",
                               f"Answered after {len(tool_calls_made)} tool call(s)."))
            return {"answer": (msg.content or "").strip(), "role": identity.role,
                    "trace": trace, "tool_calls": tool_calls_made}

        # Record the model's decision, then execute each requested tool.
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": _serialize_tool_calls(calls)})

        for tc in calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            result = execute_tool(identity, name, args)          # ← RBAC gate inside
            denied = result.get("error") == "ACCESS_DENIED"
            tool_calls_made.append(name)
            trace.append(_step(
                f"tool:{name}", name,
                "denied" if denied else "completed",
                (f"Blocked by RBAC: {result.get('reason')}" if denied
                 else f"Model called {name}({json.dumps(args)}).")
            ))

            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str)})

    # Step budget exhausted → force a best-effort answer from what we have.
    trace.append(_step("reasoner", "step_budget_reached", "completed",
                       f"Hit max_steps={max_steps}; composing from gathered data."))
    messages.append({"role": "user",
                     "content": "You have reached the tool limit. Answer now "
                                "using only the data already gathered."})
    final = client.chat.completions.create(
        model=model, messages=messages, temperature=0.2)
    return {"answer": (final.choices[0].message.content or "").strip(),
            "role": identity.role, "trace": trace, "tool_calls": tool_calls_made}
