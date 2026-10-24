"""Turn the files a researcher selects into the minimum evidence for a diagnosis.

Only the selected files are read, and only the useful parts are kept:
- logs: the first lines, lines around anomalies (NaN, Inf, errors...) and the end;
- code and config: a chosen line range ("solver.py:10-40"), or the start of the file.
Every excerpt is checked for secrets and kept within the size limits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath

from backend.orchestration.evidence_policy import MAX_ITEM_CHARS, MAX_ITEMS, MAX_TOTAL_CHARS, redact_secrets
from local_connector.file_access.grants import Grant, GrantRegistry

LOG_SUFFIXES = {".log", ".out", ".err", ".txt"}
CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".nml", ".in"}
ANOMALY = re.compile(
    r"(?i)\b(nan|inf|infinity|overflow|diverg\w*|error|exception|traceback|warning|segmentation fault)\b"
)
HEAD_LINES = 5
TAIL_LINES = 10
CONTEXT_LINES = 2
MAX_ANOMALY_HITS = 8
_SELECTION = re.compile(r"^(.*):(\d+)-(\d+)$")


@dataclass
class EvidenceItem:
    path: str  # relative to the project folder
    kind: str  # "log", "config" or "code"
    excerpt: str
    truncated: bool


@dataclass
class Evidence:
    project: str
    items: list[EvidenceItem] = field(default_factory=list)
    redactions: int = 0
    skipped: list[str] = field(default_factory=list)  # selections dropped because the size limit was reached

    @property
    def total_chars(self) -> int:
        return sum(len(i.excerpt) for i in self.items)

    def to_payload(self) -> list[dict]:
        return [{"path": i.path, "kind": i.kind, "excerpt": i.excerpt, "truncated": i.truncated} for i in self.items]


def file_kind(path: str) -> str:
    suffix = PurePath(path).suffix.lower()
    if suffix in LOG_SUFFIXES:
        return "log"
    if suffix in CONFIG_SUFFIXES:
        return "config"
    return "code"


def parse_selection(selection: str) -> tuple[str, tuple[int, int] | None]:
    """"solver.py:10-40" -> ("solver.py", (10, 40)); "run.log" -> ("run.log", None)."""
    match = _SELECTION.match(selection)
    if not match:
        return selection, None
    start, end = int(match.group(2)), int(match.group(3))
    if start < 1 or end < start:
        raise ValueError(f"Bad line range in '{selection}'. Use start-end, e.g. solver.py:10-40.")
    return match.group(1), (start, end)


def extract_evidence(registry: GrantRegistry, grant: Grant, selections: list[str]) -> Evidence:
    """Read the selected files from a granted project and keep only the relevant parts.

    Raises AccessDenied if any selection is not allowed; nothing is extracted then.
    """
    if len(selections) > MAX_ITEMS:
        raise ValueError(f"Select at most {MAX_ITEMS} files.")
    evidence = Evidence(project=grant.name)
    for selection in selections:
        path, line_range = parse_selection(selection)
        relative, text, read_truncated = registry.read_file(path, grant)
        kind = file_kind(relative)
        lines = text.splitlines()

        if line_range:
            numbered, kept_all = _numbered(lines, [line_range])
        elif kind == "log":
            numbered, kept_all = _numbered(lines, _log_ranges(lines))
        else:
            numbered, kept_all = _numbered(lines, [(1, len(lines))])
        excerpt, redacted = redact_secrets(numbered)
        evidence.redactions += redacted

        budget = min(MAX_ITEM_CHARS, MAX_TOTAL_CHARS - evidence.total_chars)
        if budget < 200:
            evidence.skipped.append(relative)
            continue
        truncated = read_truncated or not kept_all  # "truncated" = not the whole file
        if len(excerpt) > budget:
            excerpt = excerpt[: budget - 20].rstrip() + "\n  [... truncated]"
            truncated = True
        evidence.items.append(EvidenceItem(path=relative, kind=kind, excerpt=excerpt, truncated=truncated))
    return evidence


def _log_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Line ranges (1-based, inclusive) worth keeping from a log."""
    n = len(lines)
    ranges = [(1, min(HEAD_LINES, n))]
    hits = [i + 1 for i, line in enumerate(lines) if ANOMALY.search(line)]
    for hit in hits[:MAX_ANOMALY_HITS]:
        ranges.append((max(1, hit - CONTEXT_LINES), min(n, hit + CONTEXT_LINES)))
    ranges.append((max(1, n - TAIL_LINES + 1), n))
    return ranges


def _numbered(lines: list[str], ranges: list[tuple[int, int]]) -> tuple[str, bool]:
    """Render the given line ranges with line numbers, merging overlaps and marking gaps.
    Also returns whether every line of the file was kept."""
    keep: list[int] = []
    for start, end in sorted(ranges):
        for number in range(start, min(end, len(lines)) + 1):
            if not keep or number > keep[-1]:
                keep.append(number)
    out, previous = [], 0
    for number in keep:
        if number > previous + 1:
            out.append("  ...")
        out.append(f"{number:>5}| {lines[number - 1]}")
        previous = number
    if keep and keep[-1] < len(lines):
        out.append("  ...")
    return "\n".join(out), len(keep) == len(lines)
