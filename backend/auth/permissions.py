"""Project permission checks. Access policy is enforced here in code, never left to an AI agent."""
from __future__ import annotations

import sqlite3

from backend.workspaces.store import is_project_member


class PermissionDenied(Exception):
    """The member may not access this project."""


def require_project_member(conn: sqlite3.Connection, member_id: str, project_id: str) -> None:
    if not is_project_member(conn, member_id, project_id):
        raise PermissionDenied(f"{member_id} is not an active member of project {project_id}.")
