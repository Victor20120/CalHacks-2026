"""The file half of a personal agent's toolkit: list and read research files.

A CREAO agent can't reach this computer, so it asks ExpandRG in its reply, and
the backend calls these on its behalf (backend/orchestration/toolkit.py). Both
are read-only and go through GrantRegistry, so a request can only reach files in
a folder the researcher granted, never sensitive or binary files, and never
outside the folder (".." and symlinks are resolved first). Nothing is executed:
a file is only ever opened for reading. Secrets in what is read are blanked.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from backend.orchestration.evidence_policy import path_problem, redact_secrets
from local_connector.evidence.extract import _log_ranges, _numbered, file_kind
from local_connector.file_access.grants import AccessDenied, Grant, GrantRegistry
from local_connector.file_access.walk import iter_project_files

MAX_LISTED = 300
MAX_READ_LINES = 400  # per request; the agent asks for a range to see more
MAX_READ_CHARS = 12_000

# Research files an agent may read: code, build files, configs, data and logs.
READABLE_SUFFIXES = frozenset({
    ".py", ".pyx", ".ipynb", ".f", ".for", ".ftn", ".f77", ".f90", ".f95", ".f03", ".f08", ".m", ".jl", ".r",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cu", ".cuh", ".rs", ".go", ".java", ".scala", ".sh", ".bash",
    ".cmake", ".mk", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".nml", ".in", ".xml",
    ".csv", ".tsv", ".dat", ".txt", ".log", ".out", ".err", ".md", ".rst", ".tex",
})
READABLE_NAMES = frozenset({"makefile", "gnumakefile", "cmakelists.txt", "dockerfile", "readme", "requirements.txt"})
_LINES = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def is_research_file(relative: str) -> bool:
    name = PurePosixPath(relative).name.lower()
    return name in READABLE_NAMES or PurePosixPath(name).suffix in READABLE_SUFFIXES


def list_project_files(grant: Grant) -> dict:
    """The research files in a granted folder, without sensitive, binary or tool-folder files."""
    files, truncated = [], False
    for relative, full in iter_project_files(grant):
        if not is_research_file(relative):
            continue
        if len(files) >= MAX_LISTED:
            truncated = True
            break
        files.append({"path": relative, "kind": file_kind(relative), "bytes": full.stat().st_size})
    return {"folder": grant.name, "files": files, "truncated": truncated}


def read_project_file(registry: GrantRegistry, grant: Grant, path: str, lines: str | None = None) -> dict:
    """One research file, with line numbers: the requested range ("18-40" or "18"), or else the
    start of the file (for a log, its first lines, the lines around problems and its end).
    Raises AccessDenied for anything the researcher's rules don't allow, ValueError for a bad range."""
    problem = path_problem(path)  # agents name files relative to the folder, never by a local path
    if problem:
        raise AccessDenied(f"{path} {problem}.")
    resolved, relative = registry.check_path(path, grant)
    if not is_research_file(relative):
        raise AccessDenied(f"{relative} isn't a research file type ExpandRG reads (code, build files, configs, data and logs).")
    relative, text, read_from_end = registry.read_file(resolved, grant)
    all_lines = text.splitlines()
    kind = file_kind(relative)

    if lines:
        match = _LINES.match(lines)
        if not match:
            raise ValueError(f'Bad line range "{lines}". Use "18-40" or "18".')
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise ValueError(f'Bad line range "{lines}". Use "18-40" or "18".')
        end = min(end, len(all_lines), start + MAX_READ_LINES - 1)
        ranges = [(start, end)] if start <= len(all_lines) else []
    elif kind == "log":
        ranges = _log_ranges(all_lines)
    else:
        ranges = [(1, min(len(all_lines), MAX_READ_LINES))]

    numbered, whole = _numbered(all_lines, ranges) if ranges else ("", False)
    numbered, redactions = redact_secrets(numbered)
    cut = len(numbered) > MAX_READ_CHARS
    if cut:
        numbered = numbered[: MAX_READ_CHARS - 20].rstrip() + "\n  [... truncated]"
    shown = f"{ranges[0][0]}-{ranges[-1][1]}" if ranges else "none"
    result = {"folder": grant.name, "path": relative, "kind": kind, "lines": shown, "total_lines": len(all_lines),
              "truncated": cut or not whole, "redactions": redactions, "text": numbered}
    if read_from_end:
        result["note"] = "Only the end of this large file was read; line numbers count from there."
    elif lines and not ranges:
        result["note"] = f"The file has only {len(all_lines)} lines."
    return result
