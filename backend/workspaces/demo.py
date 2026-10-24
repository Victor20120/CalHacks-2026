"""Demo workspace for the prototype: one research group with a leader,
Researchers A, B and C, and two projects. Researcher B is not a member of the
wave project; the isolation demo relies on that. Researcher C exists for the
offboarding demo and comes with some private work history to hand off.

Load the demo (adds what's missing, keeps everything else):
    python -m backend.workspaces.demo
Delete the databases and start again:
    python -m backend.workspaces.demo --reset
Add a real project for testing (members: the leader plus the ones listed):
    python -m backend.workspaces.demo --add-project my-research --name "My research" --members researcher_a
"""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from backend.db.database import Database
from backend.db.schema import SHARED_TABLES
from backend.workspaces.store import InvalidProject, create_project

WORKSPACE = ("demo-lab", "Computational Physics Lab (demo)")
MEMBERS = [
    ("leader", "Group Leader", "leader"),
    ("researcher_a", "Researcher A", "researcher"),
    ("researcher_b", "Researcher B", "researcher"),
    ("researcher_c", "Researcher C", "researcher"),
]
PROJECTS = [
    ("heat-diffusion", "Heat diffusion solver", "Explicit 2D heat equation solver (tests/fixtures/sample_project)."),
    ("wave-propagation", "Wave propagation solver", "Restricted: Researcher B is not a member."),
]
MEMBERSHIP = [
    ("heat-diffusion", "leader"),
    ("heat-diffusion", "researcher_a"),
    ("heat-diffusion", "researcher_b"),
    ("heat-diffusion", "researcher_c"),
    ("wave-propagation", "leader"),
    ("wave-propagation", "researcher_a"),
]
# Each researcher's personal agent points at its own CREAO agent slot in backend/.env.
# Researcher C's agent is only there to be retired in the offboarding demo.
AGENTS = [
    ("agent-researcher_a", "researcher_a", "AGENT_1"),
    ("agent-researcher_b", "researcher_b", "AGENT_2"),
    ("agent-researcher_c", "researcher_c", "AGENT_3"),
]

# Researcher C's private work history (demo content). Offboarding should hand off
# the test result, the unresolved debugging task and the pending draft, and leave
# out the personal note and the draft C rejected.
C_EPISODES = [
    ("demo-c-ep-1", "test", "resolved",
     "Crank-Nicolson time stepping stays stable at dt = 0.05 on the 41x41 grid",
     "Ran heat2d with an implicit Crank-Nicolson step (r = 0.5): max|T| decayed monotonically to 0.31 by step "
     "3000. The explicit scheme needs dt <= 0.025 on this grid."),
    ("demo-c-ep-2", "debugging_attempt", "unresolved",
     "Boundary rows stay frozen at their initial values",
     "step() copies the edges every step but never updates them, so the boundary is an implicit fixed Dirichlet "
     "condition. Unresolved: confirm with the group which boundary condition is intended."),
    ("demo-c-ep-3", "note", "open",
     "Personal: ask about the conference travel budget",
     "Private reminder, not project work."),
]
C_DRAFTS = [
    ("demo-c-draft-1", "pending", "code_change", "Startup check refuses unstable time steps",
     "Added `assert alpha * dt / dx**2 <= 0.25` before the time loop in heat2d.py. The dt = 0.03 config now "
     "fails at startup instead of blowing up at step 2223."),
    ("demo-c-draft-2", "rejected", "research_result", "Rough idea: switch to float32 for speed",
     "Untested idea; kept private by Researcher C."),
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


def seed_demo_private(db: Database) -> None:
    """Give Researcher C some private work history for the offboarding demo. Safe to run again."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.private("researcher_c")) as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO private_episodes (id, project_id, kind, summary, details, status, created_at)
               VALUES (?, 'heat-diffusion', ?, ?, ?, ?, ?)""",
            [(e_id, kind, summary, details, status, now) for e_id, kind, status, summary, details in C_EPISODES],
        )
        conn.executemany(
            """INSERT OR IGNORE INTO share_drafts
               (id, project_id, category, title, summary, verification_status, status, created_at)
               VALUES (?, 'heat-diffusion', ?, ?, ?, 'hypothesis', ?, ?)""",
            [(d_id, category, title, summary, status, now) for d_id, status, category, title, summary in C_DRAFTS],
        )
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.workspaces.demo", description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete all ExpandRG databases first")
    parser.add_argument("--add-project", metavar="ID", help="also create a project, e.g. my-research")
    parser.add_argument("--name", help="the new project's name")
    parser.add_argument("--members", default="researcher_a", help="comma-separated member IDs (the leader is always added)")
    args = parser.parse_args(argv)

    db = Database(seed=seed_demo)
    if args.reset:
        deleted = db.reset()
        print("Deleted:", ", ".join(str(p) for p in deleted) if deleted else "nothing (no databases yet)")
    with closing(db.shared()) as conn:
        seed_demo(conn)  # also adds demo rows that are new since the database was made
        print(f"Shared database: {db.shared_path}")
        for table in SHARED_TABLES:
            print(f"  {table}: {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
    seed_demo_private(db)
    print("Researcher C's demo work history is in place for the offboarding demo.")
    if args.add_project:
        members = ["leader", *[m.strip() for m in args.members.split(",") if m.strip()]]
        with closing(db.shared()) as conn:
            try:
                project = create_project(conn, WORKSPACE[0], args.add_project, args.name or args.add_project, "", members)
            except InvalidProject as exc:
                print(f"Project not added: {exc}")
                return 1
        print(f"Added project {project['id']} ({project['name']}) with members: {', '.join(project['members'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
