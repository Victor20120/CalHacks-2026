"""Workspace, member and project lookups in the shared database.

Offboarded members have no project access: every membership check requires
the member to be active.
"""
from __future__ import annotations

import sqlite3


def list_members(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    query = "SELECT id, workspace_id, display_name, role, status FROM members"
    if active_only:
        query += " WHERE status = 'active'"
    return [dict(row) for row in conn.execute(query + " ORDER BY id")]


def get_member(conn: sqlite3.Connection, member_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, workspace_id, display_name, role, status FROM members WHERE id = ?", (member_id,)
    ).fetchone()
    return dict(row) if row else None


def projects_for_member(conn: sqlite3.Connection, member_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.id, p.workspace_id, p.name, p.description
        FROM projects p
        JOIN project_members pm ON pm.project_id = p.id
        JOIN members m ON m.id = pm.member_id
        WHERE pm.member_id = ? AND m.status = 'active'
        ORDER BY p.id
        """,
        (member_id,),
    )
    return [dict(row) for row in rows]


def is_project_member(conn: sqlite3.Connection, member_id: str, project_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM project_members pm
        JOIN members m ON m.id = pm.member_id
        WHERE pm.member_id = ? AND pm.project_id = ? AND m.status = 'active'
        """,
        (member_id, project_id),
    ).fetchone()
    return row is not None
