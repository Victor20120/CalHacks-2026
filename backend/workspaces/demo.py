"""Demo workspace for the prototype: one research group with a leader,
Researcher A and Researcher B, and two projects. Researcher B is not a member
of the wave project; the isolation demo relies on that.

Delete the databases and reload the demo:
    python -m backend.workspaces.demo --reset
"""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from backend.db.database import Database
from backend.db.schema import SHARED_TABLES

WORKSPACE = ("demo-lab", "Computational Physics Lab (demo)")
MEMBERS = [
    ("leader", "Group Leader", "leader"),
    ("researcher_a", "Researcher A", "researcher"),
    ("researcher_b", "Researcher B", "researcher"),
]
PROJECTS = [
    ("heat-diffusion", "Heat diffusion solver", "Explicit 2D heat equation solver (tests/fixtures/sample_project)."),
    ("wave-propagation", "Wave propagation solver", "Restricted: Researcher B is not a member."),
]
MEMBERSHIP = [
    ("heat-diffusion", "leader"),
    ("heat-diffusion", "researcher_a"),
    ("heat-diffusion", "researcher_b"),
    ("wave-propagation", "leader"),
    ("wave-propagation", "researcher_a"),
]
# Each researcher's personal agent points at its own CREAO agent slot in backend/.env.
AGENTS = [
    ("agent-researcher_a", "researcher_a", "AGENT_1"),
    ("agent-researcher_b", "researcher_b", "AGENT_2"),
]


def seed_demo(conn: sqlite3.Connection) -> None:
    """Insert the demo workspace. Safe to run again: existing rows are left alone."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    workspace_id = WORKSPACE[0]
    conn.execute("INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (?, ?, ?)", (*WORKSPACE, now))
    conn.executemany(
        "INSERT OR IGNORE INTO members (id, workspace_id, display_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
        [(m_id, workspace_id, name, role, now) for m_id, name, role in MEMBERS],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO projects (id, workspace_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
        [(p_id, workspace_id, name, description, now) for p_id, name, description in PROJECTS],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO project_members (project_id, member_id, added_at) VALUES (?, ?, ?)",
        [(p_id, m_id, now) for p_id, m_id in MEMBERSHIP],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO agents (id, member_id, creao_env_prefix, created_at) VALUES (?, ?, ?, ?)",
        [(a_id, m_id, prefix, now) for a_id, m_id, prefix in AGENTS],
    )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.workspaces.demo", description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete all SimGuard databases first")
    args = parser.parse_args(argv)

    db = Database(seed=seed_demo)
    if args.reset:
        deleted = db.reset()
        print("Deleted:", ", ".join(str(p) for p in deleted) if deleted else "nothing (no databases yet)")
    with closing(db.shared()) as conn:
        print(f"Shared database: {db.shared_path}")
        for table in SHARED_TABLES:
            print(f"  {table}: {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
