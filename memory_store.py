"""
memory_store.py  (Level 5 — episodic memory)

A small, persistent, *queryable* conversation memory.

The old assistant kept an in-process `_chat_history` dict that the agent could
not inspect. Here every turn is written to disk (data/agent_memory.json) and the
agent can query it with the `recall_memory` tool — e.g. "what did we discuss
earlier?" — which is the difference between transient context and real memory.

Storage is intentionally simple (a JSON file, keyword-scored recall) so it runs
with zero extra services; the interface is what matters and could be swapped for
a vector store without touching callers.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent
MEMORY_PATH = Path(__file__).with_name("data") / "agent_memory.json"

_LOCK = threading.Lock()
_STOPWORDS = {
    "the", "a", "an", "is", "are", "my", "me", "i", "what", "who", "how", "of",
    "to", "and", "for", "in", "on", "do", "did", "we", "you", "please", "show",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> Dict[str, List[Dict[str, Any]]]:
    if not MEMORY_PATH.exists():
        return {}
    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(store: Dict[str, List[Dict[str, Any]]]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(store, indent=2, default=str), encoding="utf-8")


def _key(user_id: str) -> str:
    return (user_id or "anon").strip().upper()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOPWORDS}


def record_turn(user_id: str, role: str, query: str, answer: str,
                tool_calls: List[str] | None = None) -> None:
    """Persist one completed turn to the user's episodic memory."""
    with _LOCK:
        store = _load()
        store.setdefault(_key(user_id), []).append({
            "ts": _now(),
            "role": role,
            "query": query,
            "answer": answer,
            "tool_calls": tool_calls or [],
        })
        # Keep memory bounded per user.
        store[_key(user_id)] = store[_key(user_id)][-50:]
        _save(store)


def recall(user_id: str, query: str, k: int = 3) -> List[Dict[str, Any]]:
    """Return the k most relevant past turns for this user, scored by keyword
    overlap with `query` (recency breaks ties)."""
    turns = _load().get(_key(user_id), [])
    if not turns:
        return []
    q = _tokens(query)
    scored = []
    for idx, turn in enumerate(turns):
        overlap = len(q & _tokens(turn["query"] + " " + turn["answer"]))
        scored.append((overlap, idx, turn))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [t for score, _, t in scored[:k] if score > 0]


def history_messages(user_id: str, limit: int = 4) -> List[Dict[str, str]]:
    """Recent turns rendered as chat messages, to seed a new agent run."""
    turns = _load().get(_key(user_id), [])[-limit:]
    messages: List[Dict[str, str]] = []
    for turn in turns:
        messages.append({"role": "user", "content": turn["query"]})
        messages.append({"role": "assistant", "content": turn["answer"]})
    return messages


def get_all(user_id: str) -> List[Dict[str, Any]]:
    return _load().get(_key(user_id), [])


def clear(user_id: str) -> None:
    with _LOCK:
        store = _load()
        store.pop(_key(user_id), None)
        _save(store)
