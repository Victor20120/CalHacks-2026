"""Workspace, member and project lookups in the shared database.

Offboarded members have no project access: every membership check requires
the member to be active.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")


class InvalidProject(Exception):
    pass


def create_project(conn: sqlite3.Connection, workspace_id: str, project_id: str, name: str, description: str,
                   member_ids: list[str]) -> dict:
    """Create a project in a workspace with the given members (all must be active members of it)."""
    if not _PROJECT_ID.match(project_id):
        raise InvalidProject("Project IDs use lowercase letters, digits and dashes, e.g. my-research.")
    if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
        raise InvalidProject(f"A project with the ID {project_id} already exists.")
    members = list(dict.fromkeys(member_ids))
    for member_id in members:
        member = get_member(conn, member_id)
        if member is None or member["workspace_id"] != workspace_id or member["status"] != "active":
            raise InvalidProject(f"{member_id} is not an active member of this group.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("INSERT INTO projects (id, workspace_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
                     (project_id, workspace_id, name.strip(), description.strip(), now))
        conn.executemany("INSERT INTO project_members (project_id, member_id, added_at) VALUES (?, ?, ?)",
                         [(project_id, m, now) for m in members])
    return {"id": project_id, "workspace_id": workspace_id, "name": name.strip(), "description": description.strip(),
            "members": members}


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
