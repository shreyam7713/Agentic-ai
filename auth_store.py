"""
auth_store.py

Real credential verification for /login.

Before, the login endpoint required a `password` field but never checked it —
any non-empty value logged you in as anyone (including ADM001). This closes that
hole with an actual check:

  - Per-user passwords may be set in data/credentials.json, stored ONLY as
    salted PBKDF2-HMAC-SHA256 hashes (never plaintext).
  - Any id without a stored credential must supply the shared default password
    (env MOODLE_DEFAULT_PASSWORD, default "moodle@123") — documented for the demo.

So a wrong password is now rejected with 401, while the demo stays usable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Dict

CRED_PATH = Path(__file__).with_name("data") / "credentials.json"
_DEFAULT_PASSWORD = os.getenv("MOODLE_DEFAULT_PASSWORD", "moodle@123")
_ITERATIONS = 200_000


def _hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return dk.hex()


def _load() -> Dict[str, Dict[str, str]]:
    if not CRED_PATH.exists():
        return {}
    try:
        return json.loads(CRED_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(store: Dict[str, Dict[str, str]]) -> None:
    CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRED_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")


def set_password(user_id: str, password: str) -> None:
    """Register/replace a user's password (stored as a salted hash)."""
    store = _load()
    salt = os.urandom(16)
    store[user_id.strip().upper()] = {"salt": salt.hex(), "hash": _hash(password, salt)}
    _save(store)


def verify(user_id: str, password: str) -> bool:
    """True iff `password` is correct for `user_id`.

    Uses the stored per-user hash when present, otherwise the shared default
    password. Constant-time comparison to avoid timing leaks.
    """
    if not password:
        return False
    record = _load().get((user_id or "").strip().upper())
    if record:
        expected = record["hash"]
        candidate = _hash(password, bytes.fromhex(record["salt"]))
        return hmac.compare_digest(expected, candidate)
    return hmac.compare_digest(password, _DEFAULT_PASSWORD)
