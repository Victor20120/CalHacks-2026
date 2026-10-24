"""What evidence may leave a researcher's computer.

Shared by the local connector (which reads files) and the backend (which
re-checks everything it receives before anything is sent to CREAO). Pure
standard library, so the connector can import it without the web stack.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PureWindowsPath

MAX_ITEMS = 10
MAX_ITEM_CHARS = 4_000
MAX_TOTAL_CHARS = 12_000

# Never read or sent, wherever they appear in a project.
SENSITIVE_NAMES = frozenset({
    ".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".pgpass",
    ".npmrc", ".pypirc", "credentials", "credentials.json",
    "secrets.json", "secrets.yaml", "secrets.yml",
})
SENSITIVE_PREFIXES = (".env.",)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".kdbx", ".ppk")
SENSITIVE_DIRS = frozenset({".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker"})

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
     "[redacted private key]"),
    (re.compile(r"\b(?:capi_|cr_sk_)[A-Za-z0-9_\-]{8,}"), "[redacted]"),  # CREAO keys
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),  # AWS access key IDs
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[redacted]"),  # GitHub tokens
]
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|access[_-]?key)(\s*[:=]\s*)([\"']?)([^\s\"',;]+)"
)


def is_sensitive(parts: Iterable[str]) -> bool:
    """True if any folder in the path, or the file name, is on the sensitive lists."""
    parts = [p.lower() for p in parts]
    if not parts:
        return False
    if any(p in SENSITIVE_DIRS for p in parts):
        return True
    name = parts[-1]
    return name in SENSITIVE_NAMES or name.startswith(SENSITIVE_PREFIXES) or name.endswith(SENSITIVE_SUFFIXES)


def path_problem(path: str) -> str | None:
    """Why an evidence path is not acceptable, or None if it is.

    Evidence paths must be relative to the project folder, so a local path
    (which can include the researcher's user name) never leaves the computer.
    """
    if not path.strip():
        return "is empty"
    windows = PureWindowsPath(path)
    if windows.drive or windows.root or path.startswith(("/", "\\")):
        return "must be relative to the project folder"
    parts = [p for p in re.split(r"[\\/]+", path) if p not in ("", ".")]
    if ".." in parts:
        return "must not leave the project folder"
    if is_sensitive(parts):
        return "is a sensitive file and is never sent"
    return None


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace secrets found inside text. Returns the new text and how many were replaced."""
    count = 0
    for pattern, replacement in _SECRET_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n

    def _value(match: re.Match) -> str:
        nonlocal count
        if match.group(4).startswith("[redacted"):
            return match.group(0)
        count += 1
        return f"{match.group(1)}{match.group(2)}{match.group(3)}[redacted]"

    return _ASSIGNMENT.sub(_value, text), count
