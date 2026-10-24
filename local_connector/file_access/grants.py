"""Which project folders the researcher has allowed SimGuard to read.

Grants are stored on the researcher's computer only, in
$SIMGUARD_HOME/grants.json (default: ~/.simguard/grants.json). Every read
goes through GrantRegistry.read_file, which refuses anything outside a
granted folder, sensitive files and binary files.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.orchestration.evidence_policy import path_problem

MAX_READ_BYTES = 5_000_000  # larger files are read from the end, where logs fail


class AccessDenied(Exception):
    """SimGuard is not allowed to read this."""


@dataclass(frozen=True)
class Grant:
    root: Path
    name: str  # project label sent to the backend instead of the local path
    granted_at: str


def default_state_file() -> Path:
    home = os.environ.get("SIMGUARD_HOME") or Path.home() / ".simguard"
    return Path(home) / "grants.json"


class GrantRegistry:
    def __init__(self, state_file: Path | None = None):
        self.state_file = Path(state_file) if state_file else default_state_file()
        self._grants = self._load()

    def grants(self) -> list[Grant]:
        return list(self._grants)

    def grant(self, folder: str | Path) -> Grant:
        """Allow SimGuard to read a project folder."""
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise AccessDenied(f"{root} is not a folder.")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise AccessDenied("Grant a specific project folder, not a whole drive or your home folder.")
        for existing in self._grants:
            if existing.root == root:
                return existing
        new = Grant(root=root, name=root.name, granted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._grants.append(new)
        self._save()
        return new

    def revoke(self, folder: str | Path) -> bool:
        root = Path(folder).expanduser().resolve()
        kept = [g for g in self._grants if g.root != root]
        if len(kept) == len(self._grants):
            return False
        self._grants = kept
        self._save()
        return True

    def grant_for_root(self, folder: str | Path) -> Grant:
        """The grant for exactly this project folder."""
        root = Path(folder).expanduser().resolve()
        for g in self._grants:
            if g.root == root:
                return g
        raise AccessDenied(f"{root} has not been granted. Run: python -m local_connector grant \"{root}\"")

    def check_path(self, path: str | Path, grant: Grant) -> tuple[Path, str]:
        """Check that a path is allowed (inside the granted folder, not sensitive),
        whether or not the file exists yet. Returns (resolved path, relative path)."""
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = grant.root / resolved
        resolved = resolved.resolve()  # follows symlinks and "..", so escapes are caught below
        if resolved == grant.root or grant.root not in resolved.parents:
            raise AccessDenied(f"{path} is outside the granted project folder.")
        relative = resolved.relative_to(grant.root).as_posix()
        problem = path_problem(relative)
        if problem:
            raise AccessDenied(f"{relative} {problem}.")
        return resolved, relative

    def read_file(self, path: str | Path, grant: Grant) -> tuple[str, str, bool]:
        """Read one file inside a granted folder.

        Returns (path relative to the project folder, text, truncated).
        """
        resolved, relative = self.check_path(path, grant)
        if not resolved.is_file():
            raise AccessDenied(f"{relative} is not a file.")

        size = resolved.stat().st_size
        with open(resolved, "rb") as f:
            truncated = size > MAX_READ_BYTES
            if truncated:
                f.seek(size - MAX_READ_BYTES)
            data = f.read(MAX_READ_BYTES)
        if b"\x00" in data[:8192]:
            raise AccessDenied(f"{relative} looks like a binary file.")
        return relative, data.decode("utf-8", errors="replace"), truncated

    def _load(self) -> list[Grant]:
        if not self.state_file.exists():
            return []
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        return [Grant(root=Path(g["root"]), name=g["name"], granted_at=g["granted_at"]) for g in data.get("grants", [])]

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"grants": [{"root": str(g.root), "name": g.name, "granted_at": g.granted_at} for g in self._grants]}
        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
