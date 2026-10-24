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

from backend.db.schema import PRIVATE_SCHEMA, SCHEMA_VERSION, SHARED_SCHEMA

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_MEMBER_ID = re.compile(r"^[a-z0-9_-]{1,64}$")
RESET_COMMAND = "python -m backend.workspaces.demo --reset"


class OutdatedDatabase(Exception):
    """A database file was made by an older SimGuard version."""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _create_tables(conn: sqlite3.Connection, schema: str, path: Path) -> None:
    has_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if has_tables and version != SCHEMA_VERSION:
        raise OutdatedDatabase(
            f"{path} was made by an older SimGuard version. Delete the databases and reload the demo: {RESET_COMMAND}"
        )
    conn.executescript(schema)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


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
                    _create_tables(conn, SHARED_SCHEMA, self.shared_path)
                    if self._seed:
                        self._seed(conn)
                    conn.commit()
                self._ready = True
        return connect(self.shared_path)

    def private(self, member_id: str) -> sqlite3.Connection:
        """Open one researcher's private memory database."""
        path = self.private_path(member_id)
        conn = connect(path)
        try:
            _create_tables(conn, PRIVATE_SCHEMA, path)
        except Exception:
            conn.close()
            raise
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
