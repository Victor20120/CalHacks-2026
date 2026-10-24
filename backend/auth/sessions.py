"""Prototype sign-in: pick a demo researcher and get a random session token.

Tokens live in memory and end when the backend restarts. This is not real
authentication: anyone who can reach the backend can sign in as any demo
member. The backend only listens on this computer for that reason.
"""
from __future__ import annotations

import secrets
import threading


class Sessions:
    def __init__(self) -> None:
        self._members: dict[str, str] = {}  # token -> member ID
        self._lock = threading.Lock()

    def sign_in(self, member_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._members[token] = member_id
        return token

    def member_for(self, token: str) -> str | None:
        with self._lock:
            return self._members.get(token)

    def sign_out(self, token: str) -> None:
        with self._lock:
            self._members.pop(token, None)
