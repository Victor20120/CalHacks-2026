"""SQLite persistence.

Two kinds of database, in separate files:
- superbrain.db: the group's shared Super Brain (workspace, members, projects,
  agents, approved findings and their links).
- private/<member_id>.db: one researcher's private project memory (runs and
  episodes). In the product this lives on the researcher's own computer.

Files live in $SIMGUARD_DATA_DIR (default: backend/data/), which git ignores.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Callable

from backend.db.schema import PRIVATE_SCHEMA, SHARED_SCHEMA

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_MEMBER_ID = re.compile(r"^[a-z0-9_-]{1,64}$")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class Database:
    """Opens the shared and private databases. Callers close what they open."""

    def __init__(self, data_dir: str | Path | None = None, seed: Callable[[sqlite3.Connection], None] | None = None):
        self._data_dir = Path(data_dir) if data_dir else None
        self._seed = seed
        self._ready = False
        self._lock = threading.Lock()

    @property
    def data_dir(self) -> Path:
        return self._data_dir or Path(os.environ.get("SIMGUARD_DATA_DIR") or DEFAULT_DATA_DIR)

    @property
    def shared_path(self) -> Path:
        return self.data_dir / "superbrain.db"

    def private_path(self, member_id: str) -> Path:
        if not _MEMBER_ID.match(member_id):
            raise ValueError(f"Invalid member ID: {member_id!r}")
        return self.data_dir / "private" / f"{member_id}.db"

    def shared(self) -> sqlite3.Connection:
        """Open the shared Super Brain database, creating its tables (and the seed data) on first use."""
        with self._lock:
            if not self._ready:
                with closing(connect(self.shared_path)) as conn:
                    conn.executescript(SHARED_SCHEMA)
                    if self._seed:
                        self._seed(conn)
                    conn.commit()
                self._ready = True
        return connect(self.shared_path)

    def private(self, member_id: str) -> sqlite3.Connection:
        """Open one researcher's private memory database."""
        conn = connect(self.private_path(member_id))
        conn.executescript(PRIVATE_SCHEMA)
        return conn

    def reset(self) -> list[Path]:
        """Delete every database file in the data folder. Returns the files deleted."""
        with self._lock:
            files = [self.shared_path, *sorted((self.data_dir / "private").glob("*.db"))]
            deleted = [path for path in files if path.exists()]
            for path in deleted:
                path.unlink()
            self._ready = False
        return deleted
