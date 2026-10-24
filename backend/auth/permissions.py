"""Project permission checks. Access policy is enforced here in code, never left to an AI agent."""
from __future__ import annotations

import sqlite3

from backend.workspaces.store import is_project_member


class PermissionDenied(Exception):
    """The member may not access this project."""


def require_project_member(conn: sqlite3.Connection, member_id: str, project_id: str) -> None:
    if not is_project_member(conn, member_id, project_id):
        raise PermissionDenied(f"{member_id} is not an active member of project {project_id}.")


def context_project_ids(conn: sqlite3.Connection, project_id: str) -> list[str]:
    """Projects whose findings may inform a diagnosis in project_id.

    A diagnosis can become a finding in project_id that all its members read, so
    it may only draw on projects every one of those members can already read.
    Otherwise a summary could repeat restricted details to someone without access.
    """
    def active_members(pid: str) -> set[str]:
        rows = conn.execute(
            """SELECT pm.member_id FROM project_members pm JOIN members m ON m.id = pm.member_id
               WHERE pm.project_id = ? AND m.status = 'active'""",
            (pid,),
        )
        return {r[0] for r in rows}

    readers = active_members(project_id)
    candidates = conn.execute(
        "SELECT id FROM projects WHERE workspace_id = (SELECT workspace_id FROM projects WHERE id = ?) ORDER BY id",
        (project_id,),
    )
    return [r[0] for r in candidates if readers <= active_members(r[0])]
