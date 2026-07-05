"""
reflection.py  (Level 5 — self-correction)

A critic pass. After the agent drafts an answer, a second LLM call — given the
raw tool observations and the draft — judges whether every claim in the draft is
actually supported by the retrieved data. If not, it returns a corrected answer
grounded only in those observations.

This is the "reflect → retry" pattern: the system checks its own work against
evidence before returning it, instead of trusting the first draft.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

_CRITIC_SYSTEM = (
    "You are a strict fact-checking critic for an academic assistant. You are "
    "given the DATA the tools returned and a DRAFT answer. Decide whether every "
    "factual claim in the draft (names, numbers, ids, emails) is supported by the "
    "DATA. Do not add new facts. Respond with ONLY a JSON object: "
    '{"supported": true|false, "issues": ["..."], "corrected_answer": "..."}. '
    "If supported is true, set corrected_answer to the draft unchanged."
)


def _extract_json(text: str) -> Dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def critique(
    client: Any,
    *,
    query: str,
    observations: List[Dict[str, Any]],
    draft_answer: str,
    model: str = "llama-3.3-70b-versatile",
) -> Dict[str, Any]:
    """Return {supported, issues, answer} — `answer` is the (possibly corrected)
    final text. Fails open (keeps the draft) if the critic errors, so reflection
    can never make the system worse."""
    # No tools were used → nothing to fact-check against; skip the call.
    if not observations:
        return {"supported": True, "issues": [], "answer": draft_answer, "skipped": True}

    data_blob = json.dumps(observations, default=str)[:6000]
    user = (
        f"USER QUESTION:\n{query}\n\n"
        f"DATA (tool observations):\n{data_blob}\n\n"
        f"DRAFT ANSWER:\n{draft_answer}\n\n"
        "Fact-check the draft against DATA and return the JSON object."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _CRITIC_SYSTEM},
                      {"role": "user", "content": user}],
            temperature=0.0,
        )
        parsed = _extract_json(resp.choices[0].message.content or "")
    except Exception:
        parsed = None

    if not parsed:
        return {"supported": True, "issues": [], "answer": draft_answer, "skipped": True}

    supported = bool(parsed.get("supported", True))
    corrected = (parsed.get("corrected_answer") or draft_answer).strip()
    return {
        "supported": supported,
        "issues": parsed.get("issues", []) or [],
        "answer": draft_answer if supported else corrected,
    }
