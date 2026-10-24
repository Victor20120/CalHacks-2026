"""Reading approved findings from the shared Super Brain.

Callers check project membership first (backend.auth.permissions).
Search across projects and following links come in build step 5.
"""
from __future__ import annotations

import sqlite3

_FINDING_COLUMNS = """
    f.id, f.workspace_id, f.project_id, f.category, f.title, f.summary, f.verification_status,
    f.contributed_by, m.display_name AS contributed_by_name, f.source_creao_run_id, f.created_at,
    a.edited AS edited_before_approval, a.approved_at
"""
_FROM = """
    FROM shared_findings f
    JOIN members m ON m.id = f.contributed_by
    JOIN approvals a ON a.id = f.approval_id
"""


def list_project_findings(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_FINDING_COLUMNS} {_FROM} WHERE f.project_id = ? ORDER BY f.created_at DESC, f.id", (project_id,)
    )
    return [_row(r) for r in rows]


def get_finding(conn: sqlite3.Connection, finding_id: str) -> dict | None:
    row = conn.execute(f"SELECT {_FINDING_COLUMNS} {_FROM} WHERE f.id = ?", (finding_id,)).fetchone()
    return _row(row) if row else None


def _row(row: sqlite3.Row) -> dict:
    finding = dict(row)
    finding["edited_before_approval"] = bool(finding["edited_before_approval"])
    return finding
