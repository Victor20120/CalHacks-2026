"""Walk a granted project folder, read-only, skipping what must never be offered:
sensitive files (.env, keys, .git, ...), binary files, and tool folders such as
virtual environments and node_modules."""
from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from backend.orchestration.evidence_policy import path_problem
from local_connector.file_access.grants import Grant

SKIP_DIRS = {
    "node_modules", "__pycache__", ".venv", "venv", "env", ".env", ".mypy_cache", ".pytest_cache",
    ".ipynb_checkpoints", ".idea", ".vscode", ".tox", "build", "dist", ".eggs",
}


@dataclass
class WalkStats:
    skipped: Counter = field(default_factory=Counter)  # reason -> count


def iter_project_files(grant: Grant, stats: WalkStats | None = None) -> Iterator[tuple[str, Path]]:
    """Yield (path relative to the project, full path) for every file that may be offered."""
    stats = stats or WalkStats()
    for dirpath, dirnames, filenames in os.walk(grant.root):
        rel_dir = Path(dirpath).relative_to(grant.root)
        kept = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS or d.endswith(".egg-info"):
                stats.skipped["tool folders"] += 1
            elif path_problem((rel_dir / d / "x").as_posix()):
                stats.skipped["sensitive folders"] += 1
            elif is_link(Path(dirpath) / d):  # os.walk follows Windows junctions, which can lead anywhere
                stats.skipped["linked folders"] += 1
            else:
                kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            relative = (rel_dir / name).as_posix()
            full = Path(dirpath) / name
            if path_problem(relative):
                stats.skipped["sensitive files"] += 1
            elif not inside(grant.root, full):
                stats.skipped["links outside the folder"] += 1
            elif looks_binary(full):
                stats.skipped["binary files"] += 1
            else:
                yield relative, full


def is_link(path: Path) -> bool:
    return path.is_symlink() or os.path.isjunction(path)


def inside(root: Path, path: Path) -> bool:
    """True if the path, with links followed, is still inside the granted folder."""
    try:
        return root in path.resolve().parents
    except OSError:
        return False


def looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(1024)
    except OSError:
        return True
