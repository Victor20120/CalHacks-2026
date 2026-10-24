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


def list_readable_findings(
    conn: sqlite3.Connection, member_id: str, project_ids: list[str] | None = None
) -> list[dict]:
    """Every finding the member may read, across their projects, newest first.
    project_ids narrows the list further; it never widens it."""
    readable = _readable_rows(conn, member_id, project_ids, " ORDER BY f.created_at DESC, f.rowid DESC")
    return [_row(r) for r in readable]


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
    wanted = _terms(query)
    results = []
    for row in _readable_rows(conn, member_id, project_ids):
        finding = _row(row)
        score = 2 * len(wanted & _terms(finding["title"])) + len(wanted & _terms(finding["summary"]))
        if score >= min_score:
            results.append({**finding, "score": score})
    results.sort(key=lambda f: (f["score"], f["created_at"]), reverse=True)  # best match, then newest
    return results[:limit]


def select_chat_findings(conn: sqlite3.Connection, member_id: str, query: str,
                         project_ids: list[str], previous_sources: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Retrieve from everyone's approved knowledge, with broad-query coverage.

    Unlike keyword search, an overview request or short follow-up must not yield
    an empty brain just because it shares no words with a finding's title.
    """
    readable = list_readable_findings(conn, member_id, project_ids)
    stats = {"findings": len(readable), "researchers": len({f["contributed_by"] for f in readable}),
             "projects": len({f["project_id"] for f in readable})}
    wanted = _terms(query)
    previous_ids = {f["id"] for f in previous_sources or []}
    overview = bool(re.search(
        r"\b(?:overview|what (?:do|does) (?:we|the (?:group|team)) know|"
        r"what (?:have we|has (?:everyone|the (?:group|team))) (?:learned|found|shared)|"
        r"summari[sz]e (?:all|our (?:research|findings)|(?:the )?(?:group|team)|everyone)|"
        r"compare (?:everyone|all researchers))\b", query, re.I))
    scored = []
    for finding in readable:
        score = 2 * len(wanted & _terms(finding["title"])) + len(wanted & _terms(finding["summary"]))
        if finding["contributed_by_name"].lower() in query.lower():
            score += 5
        if len(wanted) <= 4 and finding["id"] in previous_ids:
            score += 2
        if score:
            scored.append((score, finding))
    if scored and not overview:
        scored.sort(key=lambda item: (item[0], item[1]["created_at"]), reverse=True)
        selected = [f for _, f in scored[:8]]
    else:
        # Round-robin across contributor/project pairs, so one busy agent cannot
        # fill every slot and hide what the other researchers have learned.
        buckets: dict[tuple[str, str], list[dict]] = {}
        for finding in readable:
            buckets.setdefault((finding["contributed_by"], finding["project_id"]), []).append(finding)
        selected = []
        while len(selected) < 8 and any(buckets.values()):
            for bucket in buckets.values():
                if bucket and len(selected) < 8:
                    selected.append(bucket.pop(0))
    return selected, stats


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


def _readable_rows(
    conn: sqlite3.Connection, member_id: str, project_ids: list[str] | None, order_by: str = ""
) -> list[sqlite3.Row]:
    """Rows of findings in the active member's projects, optionally narrowed to project_ids."""
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
    return conn.execute(sql + order_by, params).fetchall()


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
