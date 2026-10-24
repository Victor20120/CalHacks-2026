"""Simulation runs, as watched on a researcher's computer.

While a researcher watches a run log, their local connector sends the numbers it
reads from each step (for example max|T| and residual) and the run's alerts, so
the run shows live on the dashboard and can be opened again later. The log file
itself never leaves their computer.

Who sees a run: the researcher who watched it, and the group leader if they are
an active member of the run's project. Other researchers never do. Access is
decided in SQL, so a run you can't see looks like it doesn't exist.
"""
from __future__ import annotations

import math
import sqlite3
import uuid
from datetime import datetime, timezone

from backend.monitoring.alerts import clean_alert

MAX_METRICS_PER_RUN = 6  # new names past this are ignored
MAX_POINTS_PER_METRIC = 1_000  # past this, every other point is dropped (the whole run stays in view)
MAX_ALERTS_PER_RUN = 200
MAX_METRIC_CHARS = 40
MAX_LABEL_CHARS = 200

_VISIBLE = """
    (r.member_id = :viewer OR (:is_leader AND EXISTS (
        SELECT 1 FROM project_members pm JOIN members me ON me.id = pm.member_id
        WHERE pm.project_id = r.project_id AND pm.member_id = :viewer AND me.status = 'active')))
"""
_RUN_COLUMNS = """
    r.id, r.project_id, r.member_id, m.display_name AS member_name, r.run_label, r.watching, r.last_step,
    r.started_at, r.updated_at,
    (SELECT COUNT(*) FROM run_alerts a WHERE a.run_id = r.id AND a.severity = 'critical') AS critical,
    (SELECT COUNT(*) FROM run_alerts a WHERE a.run_id = r.id AND a.severity = 'warning') AS warning,
    (SELECT COUNT(*) FROM run_alerts a WHERE a.run_id = r.id AND a.severity = 'informational') AS informational
"""


class RunNotFound(Exception):
    pass


def create_run(conn: sqlite3.Connection, member: dict, project_id: str, run_label: str) -> dict:
    """Start recording a watched run. The caller checks project membership first."""
    now = _now()
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO simulation_runs (id, workspace_id, project_id, member_id, run_label, started_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, member["workspace_id"], project_id, member["id"], run_label[:MAX_LABEL_CHARS], now, now),
    )
    conn.commit()
    return get_visible_run(conn, member, run_id)


def add_report(
    conn: sqlite3.Connection,
    member_id: str,
    run_id: str,
    points: list[dict],
    alerts: list[dict],
    stopped: bool = False,
) -> dict:
    """New numbers and alerts for a run, from the researcher watching it.
    points: [{"step": 200, "values": {"max|T|": 2.75e14, "residual": None}}, ...]
    (None, NaN or Inf is stored as "non-finite"). Raises RunNotFound for anyone else's run."""
    row = conn.execute("SELECT member_id FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None or row["member_id"] != member_id:
        raise RunNotFound("No run with that ID.")
    cleaned = [clean_alert(a) for a in alerts]  # refuses bad alerts before anything is stored

    known = [r["metric"] for r in conn.execute("SELECT DISTINCT metric FROM run_points WHERE run_id = ?", (run_id,))]
    rows, touched = [], set()
    for point in points:
        for name, value in point["values"].items():
            name = name[:MAX_METRIC_CHARS]
            if name not in known:
                if len(known) >= MAX_METRICS_PER_RUN:
                    continue
                known.append(name)
            number = value if isinstance(value, (int, float)) and math.isfinite(value) else None
            rows.append((run_id, name, int(point["step"]), number))
            touched.add(name)
    conn.executemany("INSERT INTO run_points (run_id, metric, step, value) VALUES (?, ?, ?, ?)", rows)
    for name in touched:
        _thin(conn, run_id, name)

    stored = conn.execute("SELECT COUNT(*) FROM run_alerts WHERE run_id = ?", (run_id,)).fetchone()[0]
    now = _now()
    conn.executemany(
        "INSERT INTO run_alerts (id, run_id, severity, check_name, message, metric, step, line_number, line, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(str(uuid.uuid4()), run_id, a["severity"], a["check"], a["message"], a["metric"], a["step"],
          a["line_number"], a["line"], now) for a in cleaned[: max(0, MAX_ALERTS_PER_RUN - stored)]],
    )
    steps = [int(p["step"]) for p in points]
    conn.execute(
        "UPDATE simulation_runs SET updated_at = ?, last_step = MAX(COALESCE(last_step, 0), ?),"
        " watching = CASE WHEN ? THEN 0 ELSE watching END WHERE id = ?",
        (now, max(steps, default=0), stopped, run_id),
    )
    conn.commit()
    return {"points": len(rows), "alerts": len(cleaned)}


def visible_runs(conn: sqlite3.Connection, viewer: dict, project_ids: list[str] | None = None) -> list[dict]:
    """Runs the viewer may see, newest first. project_ids narrows the list; it never widens it."""
    sql = f"SELECT {_RUN_COLUMNS} FROM simulation_runs r JOIN members m ON m.id = r.member_id WHERE {_VISIBLE}"
    params: dict = {"viewer": viewer["id"], "is_leader": viewer["role"] == "leader"}
    if project_ids is not None:
        if not project_ids:
            return []
        names = [f":p{i}" for i in range(len(project_ids))]
        sql += f" AND r.project_id IN ({', '.join(names)})"
        params |= {f"p{i}": p for i, p in enumerate(project_ids)}
    rows = conn.execute(sql + " ORDER BY r.started_at DESC, r.rowid DESC", params).fetchall()
    return [_run(conn, r) for r in rows]


def get_visible_run(conn: sqlite3.Connection, viewer: dict, run_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {_RUN_COLUMNS} FROM simulation_runs r JOIN members m ON m.id = r.member_id"
        f" WHERE r.id = :run_id AND {_VISIBLE}",
        {"run_id": run_id, "viewer": viewer["id"], "is_leader": viewer["role"] == "leader"},
    ).fetchone()
    return _run(conn, row) if row else None


def run_detail(conn: sqlite3.Connection, viewer: dict, run_id: str) -> dict | None:
    """A run with its numbers per metric (in the order they first appeared) and its alerts."""
    run = get_visible_run(conn, viewer, run_id)
    if run is None:
        return None
    series: dict[str, list[list]] = {name: [] for name in run["metrics"]}
    for r in conn.execute("SELECT metric, step, value FROM run_points WHERE run_id = ? ORDER BY step, rowid", (run_id,)):
        series[r["metric"]].append([r["step"], r["value"]])
    alerts = conn.execute(
        "SELECT id, severity, check_name AS \"check\", message, metric, step, line_number, line, created_at"
        " FROM run_alerts WHERE run_id = ? ORDER BY created_at, rowid",
        (run_id,),
    )
    return {**run, "series": series, "alerts": [dict(a) for a in alerts]}


def recent_alerts(conn: sqlite3.Connection, viewer: dict, limit: int = 20) -> list[dict]:
    """The newest alerts from runs the viewer may see, with who and which run."""
    rows = conn.execute(
        f"""SELECT a.id, a.severity, a.check_name AS "check", a.message, a.metric, a.step, a.created_at,
                   r.id AS run_id, r.project_id, r.run_label, m.display_name AS member_name
            FROM run_alerts a JOIN simulation_runs r ON r.id = a.run_id JOIN members m ON m.id = r.member_id
            WHERE {_VISIBLE} ORDER BY a.created_at DESC, a.rowid DESC LIMIT :limit""",
        {"viewer": viewer["id"], "is_leader": viewer["role"] == "leader", "limit": limit},
    )
    return [dict(r) for r in rows]


def _thin(conn: sqlite3.Connection, run_id: str, metric: str) -> None:
    """Over the limit, drop every other point, keeping the last one and every NaN/Inf point."""
    rows = conn.execute(
        "SELECT rowid, value FROM run_points WHERE run_id = ? AND metric = ? ORDER BY step, rowid", (run_id, metric)
    ).fetchall()
    if len(rows) <= MAX_POINTS_PER_METRIC:
        return
    drop = [(r["rowid"],) for i, r in enumerate(rows[:-1]) if i % 2 == 1 and r["value"] is not None]
    conn.executemany("DELETE FROM run_points WHERE rowid = ?", drop)


def _run(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    run = dict(row)
    run["watching"] = bool(run["watching"])
    run["metrics"] = [r["metric"] for r in conn.execute(
        "SELECT metric FROM run_points WHERE run_id = ? GROUP BY metric ORDER BY MIN(rowid)", (run["id"],))]
    return run


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
