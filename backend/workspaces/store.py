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


def rename_project(conn: sqlite3.Connection, project_id: str, name: str) -> dict:
    """A project's new name, for everyone in the group; its ID (and so everything filed under it) stays.
    Returns {"id", "name", "members"}, as create_project does."""
    name = " ".join(name.split())[:100]
    if not name:
        raise InvalidProject("A project needs a name.")
    with conn:
        if conn.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id)).rowcount != 1:
            raise InvalidProject(f"There is no project {project_id}.")
    members = [r[0] for r in conn.execute("SELECT member_id FROM project_members WHERE project_id = ? ORDER BY rowid", (project_id,))]
    return {"id": project_id, "name": name, "members": members}


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


def personal_agent_for_member(conn: sqlite3.Connection, member_id: str) -> dict | None:
    """An active personal agent, numbered by first activation in its workspace.

    Agent registration is activation in this prototype. Keep retired records in
    the ordering so offboarding never renumbers the remaining agents. Creation
    order breaks ties for agents registered in the same second (the demo seed).
    The CREAO environment prefix chooses credentials, never the public number.
    """
    row = conn.execute(
        """WITH numbered AS (
            SELECT a.id, a.member_id, a.creao_env_prefix, a.status, m.status AS member_status,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.workspace_id ORDER BY a.created_at, a.rowid
                   ) AS number
            FROM agents a JOIN members m ON m.id = a.member_id
        )
        SELECT id, creao_env_prefix, number FROM numbered
        WHERE member_id = ? AND status = 'active' AND member_status = 'active'""",
        (member_id,),
    ).fetchone()
    return {**dict(row), "name": f"Agent {row['number']}"} if row else None


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
