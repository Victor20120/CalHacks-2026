"""Small, deterministic wire types and safety boundaries for research context."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

from backend.orchestration.evidence_policy import path_problem, redact_secrets


class ContextError(ValueError):
    pass


class ContextNotFound(ContextError):
    """Absent and unauthorized records deliberately have the same response."""


class ContextConflict(ContextError):
    pass


PREFIXES = frozenset({"run", "exp", "base", "sim", "anomaly", "investigation", "finding", "graph", "draft", "artifact", "worksheet"})
PRIVATE_PREFIXES = frozenset({"exp", "base", "sim", "investigation", "graph", "draft", "artifact"})
_REF = re.compile(r"^([a-z]+):([A-Za-z0-9_.-]{1,160})$")
_ABSOLUTE = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/][^\s\"'<>]*|/(?:home|Users|mnt|tmp|private|root|var|etc)/[^\s\"'<>]*)")
_UNSAFE_KEYS = frozenset({"conversation", "comments", "human_comments", "credentials", "credential", "env", "environment", "environ", "secrets", "settings", "api_key", "access_token", "password", "token", "absolute_path", "root", "project_root", "cwd", "command", "commands", "stdout", "stderr", "raw_contents", "contents", "raw_output"})


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def split_ref(ref: str) -> tuple[str, str]:
    match = _REF.fullmatch(ref) if isinstance(ref, str) else None
    if not match or match[1] not in PREFIXES:
        raise ContextError("Use a typed research reference, for example run:<id>.")
    return match[1], match[2]


def json_load(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {} if default is None else default


def canonical(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError):
        raise ContextError("Context metadata must contain finite JSON values.") from None


def safe_text(value: str, maximum: int = 1800) -> str:
    text, _ = redact_secrets(value)
    return _ABSOLUTE.sub("[local path]", text)[:maximum]


def sanitize(value, *, depth: int = 0):
    """Bound metadata and remove secrets, raw streams, and local absolute paths.

    This is deliberately not a file-read grant. Locators only identify captured
    evidence; the existing connector must authorize any subsequent content read.
    """
    if depth > 6:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return safe_text(value, 2200)
    if isinstance(value, list):
        return [sanitize(v, depth=depth + 1) for v in value[:30]]
    if isinstance(value, dict):
        result = {}
        for key, child in list(value.items())[:60]:
            if not isinstance(key, str):
                continue
            normalized = key.lower().replace("-", "_")
            if normalized in _UNSAFE_KEYS or any(word in normalized for word in ("password", "credential", "secret", "api_key", "access_token")):
                continue
            if normalized in {"path", "relative_path", "filename"} and (not isinstance(child, str) or path_problem(child)):
                continue
            result[safe_text(key, 100)] = sanitize(child, depth=depth + 1)
        return result
    return None


def safe_locator(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("record_ref", "role", "relative_path", "artifact_id", "evidence_id"):
        child = value.get(key)
        if isinstance(child, str) and len(child) <= 240:
            if key == "relative_path" and path_problem(child):
                continue
            result[key] = safe_text(child, 240)
    return result or None
