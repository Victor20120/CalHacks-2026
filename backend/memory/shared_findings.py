"""Reading approved findings from the shared Super Brain.

Access is decided in SQL before anything is ranked or returned: a member only
ever sees findings in projects they are an active member of, and links are
only followed to findings they could open directly.
"""
from __future__ import annotations

import re
import sqlite3

from backend.workspaces.store import is_project_member

_FINDING_COLUMNS = """
    f.id, f.workspace_id, f.project_id, f.category, f.title, f.summary, f.verification_status,
    f.contributed_by, m.display_name AS contributed_by_name, f.source_creao_run_id, f.created_at,
    a.edited AS edited_before_approval, a.approved_at, hr.finding_id IS NOT NULL AS handed_off
"""
_FROM = """
    FROM shared_findings f
    JOIN members m ON m.id = f.contributed_by
    JOIN approvals a ON a.id = f.approval_id
    LEFT JOIN handoff_records hr ON hr.finding_id = f.id
"""
_TOKEN = re.compile(r"[a-z0-9_.]+")
_STOPWORDS = frozenset(
    "a an and are as at be been but by can for from had has have if in into is it its not no of on or so "
    "than that the then there this to was we were what when which while will with you your".split()
)


def list_project_findings(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """All findings in one project. The caller checks membership first."""
    rows = conn.execute(
        f"SELECT {_FINDING_COLUMNS} {_FROM} WHERE f.project_id = ? ORDER BY f.created_at DESC, f.rowid DESC", (project_id,)
    )
    return [_row(r) for r in rows]


def get_finding(conn: sqlite3.Connection, finding_id: str) -> dict | None:
    """One finding, without an access check. Use get_readable_finding for members."""
    row = conn.execute(f"SELECT {_FINDING_COLUMNS} {_FROM} WHERE f.id = ?", (finding_id,)).fetchone()
    return _row(row) if row else None


def get_readable_finding(conn: sqlite3.Connection, member_id: str, finding_id: str) -> dict | None:
    """The finding if the member may read it, else None (as if it didn't exist)."""
    finding = get_finding(conn, finding_id)
    if finding is None or not is_project_member(conn, member_id, finding["project_id"]):
        return None
    return finding


def search_findings(
    conn: sqlite3.Connection,
    member_id: str,
    query: str,
    project_ids: list[str] | None = None,
    limit: int = 5,
    min_score: int = 2,
) -> list[dict]:
    """Approved findings the member may read, ranked by words shared with the query
    (a shared word counts 2 in the title, 1 in the summary). project_ids narrows
    the search further; it never widens it."""
    sql = f"""
        SELECT {_FINDING_COLUMNS} {_FROM}
        JOIN project_members reader ON reader.project_id = f.project_id
        JOIN members me ON me.id = reader.member_id
        WHERE reader.member_id = ? AND me.status = 'active'
    """
    params: list = [member_id]
    if project_ids is not None:
        if not project_ids:
            return []
        sql += f" AND f.project_id IN ({','.join('?' * len(project_ids))})"
        params += project_ids

    wanted = _terms(query)
    results = []
    for row in conn.execute(sql, params):
        finding = _row(row)
        score = 2 * len(wanted & _terms(finding["title"])) + len(wanted & _terms(finding["summary"]))
        if score >= min_score:
            results.append({**finding, "score": score})
    results.sort(key=lambda f: (f["score"], f["created_at"]), reverse=True)  # best match, then newest
    return results[:limit]


def related_findings(conn: sqlite3.Connection, member_id: str, finding_id: str) -> list[dict]:
    """Findings linked to this one, in either direction, that the member may read.
    Links into projects they can't read are left out entirely."""
    rows = conn.execute(
        """SELECT to_finding_id AS other, relation, 'outgoing' AS direction FROM finding_links WHERE from_finding_id = ?
           UNION ALL
           SELECT from_finding_id, relation, 'incoming' FROM finding_links WHERE to_finding_id = ?""",
        (finding_id, finding_id),
    )
    related = []
    for row in rows.fetchall():
        other = get_readable_finding(conn, member_id, row["other"])
        if other is not None:
            related.append({"relation": row["relation"], "direction": row["direction"], "finding": other})
    return related


def _terms(text: str) -> set[str]:
    terms = set()
    for token in _TOKEN.findall(text.lower()):
        token = token.strip("._")
        if len(token) >= 2 and token not in _STOPWORDS:
            terms.add(token)
    return terms


def _row(row: sqlite3.Row) -> dict:
    finding = dict(row)
    finding["edited_before_approval"] = bool(finding["edited_before_approval"])
    finding["handed_off"] = bool(finding["handed_off"])  # from an offboarding handoff, not approved by its author
    return finding
