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

import hashlib
import json
import math
import re
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
    (r.member_id = :viewer OR :is_leader) AND EXISTS (
        SELECT 1 FROM project_members pm JOIN members me ON me.id = pm.member_id
        WHERE pm.project_id = r.project_id AND pm.member_id = :viewer AND me.status = 'active')
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


class ReportConflict(ValueError):
    """An idempotency key was reused for a different canonical request."""


def create_run(conn: sqlite3.Connection, member: dict, project_id: str, run_label: str,
               client_run_id: str | None = None) -> dict:
    """Start a watched run once when the producer supplies a stable client ID.

    Membership is rechecked here as well as by the API, including on retries.
    Domain data and its context receipt commit together.
    """
    label = run_label[:MAX_LABEL_CHARS]
    client_run_id = _key(client_run_id, "client_run_id")
    run_id = (str(uuid.uuid5(uuid.NAMESPACE_URL, f"expandrg:run:{member['id']}:{project_id}:{client_run_id}"))
              if client_run_id else str(uuid.uuid4()))
    producer = f"monitor:{member['id']}"
    event = f"run.started:{project_id}:{client_run_id}" if client_run_id else None
    digest = _digest({"member_id": member["id"], "project_id": project_id, "run_label": label})
    _begin(conn)
    with conn:
        if not _active_member(conn, member["id"], project_id):
            raise RunNotFound("No active access to that project.")
        previous = _receipt(conn, producer, event, digest) if event else None
        if previous is not None:
            return get_visible_run(conn, member, previous["run_id"])
        now = _now()
        conn.execute(
            "INSERT INTO simulation_runs (id, workspace_id, project_id, member_id, run_label, started_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, member["workspace_id"], project_id, member["id"], label, now, now),
        )
        if event:
            _save_receipt(conn, producer, event, project_id, member["id"], "run.started", run_id, 0,
                          digest, {"run_id": run_id}, now)
    return get_visible_run(conn, member, run_id)


def add_report(
    conn: sqlite3.Connection,
    member_id: str,
    run_id: str,
    points: list[dict],
    alerts: list[dict],
    stopped: bool = False,
    batch_id: str | None = None,
    producer_id: str | None = None,
    source_revision: int | None = None,
) -> dict:
    """Persist a report and its receipt atomically, never duplicating an ACK retry.

    Frozen producer batches use stable batch IDs and increasing source revisions.
    A stale revision may add missing historical samples, but cannot overwrite a
    sample already accepted from a newer batch or change the run's lifecycle.
    Legacy callers keep working without keys; repeated metric/step values still
    merge rather than accumulating duplicate observations.
    """
    batch_id = _key(batch_id, "batch_id")
    producer_id = _key(producer_id, "producer_id")
    if source_revision is not None and (not isinstance(source_revision, int) or isinstance(source_revision, bool)
                                         or source_revision < 0):
        raise ValueError("source_revision must be a nonnegative integer.")
    cleaned = [clean_alert(a) for a in alerts]
    normalized = _points(points)
    revision = source_revision or 0
    producer = f"monitor:{member_id}:{producer_id or 'default'}"
    event = f"run.reported:{run_id}:{batch_id}" if batch_id else None
    digest = _digest({"run_id": run_id, "points": normalized, "alerts": cleaned,
                      "stopped": bool(stopped), "source_revision": source_revision})
    _begin(conn)
    with conn:
        row = conn.execute("SELECT member_id, project_id FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None or row["member_id"] != member_id or not _active_member(conn, member_id, row["project_id"]):
            raise RunNotFound("No run with that ID.")
        previous = _receipt(conn, producer, event, digest) if event else None
        if previous is not None:
            return previous["ack"]
        newest = conn.execute(
            "SELECT MAX(source_revision) FROM context_events WHERE producer_id = ? AND subject_ref = ?"
            " AND event_type = 'run.reported' AND state = 'applied'",
            (producer, f"run:{run_id}"),
        ).fetchone()[0] if batch_id and source_revision is not None else None
        stale = newest is not None and revision < newest
        known = [r["metric"] for r in conn.execute(
            "SELECT metric FROM run_points WHERE run_id = ? GROUP BY metric ORDER BY MIN(rowid)", (run_id,))]
        accepted, touched = 0, set()
        # Last value for the same key within a batch wins deterministically.
        values = {(name, point["step"]): value for point in normalized for name, value in point["values"].items()}
        for (name, step), value in values.items():
            if name not in known:
                if len(known) >= MAX_METRICS_PER_RUN:
                    continue
                known.append(name)
            rows = conn.execute("SELECT rowid FROM run_points WHERE run_id = ? AND metric = ? AND step = ?"
                                " ORDER BY rowid", (run_id, name, step)).fetchall()
            if rows:
                if stale:
                    continue
                keeper = rows[0]["rowid"]
                conn.execute("UPDATE run_points SET value = ? WHERE rowid = ?", (value, keeper))
                conn.execute("DELETE FROM run_points WHERE run_id = ? AND metric = ? AND step = ? AND rowid != ?",
                             (run_id, name, step, keeper))
            else:
                conn.execute("INSERT INTO run_points (run_id, metric, step, value) VALUES (?, ?, ?, ?)",
                             (run_id, name, step, value))
            accepted += 1
            touched.add(name)
        for name in touched:
            _thin(conn, run_id, name)

        stored = conn.execute("SELECT COUNT(*) FROM run_alerts WHERE run_id = ?", (run_id,)).fetchone()[0]
        now, anomaly_refs = _now(), []
        for index, alert in enumerate(cleaned[:max(0, MAX_ALERTS_PER_RUN - stored)]):
            anomaly_id = (str(uuid.uuid5(uuid.NAMESPACE_URL, f"expandrg:anomaly:{producer}:{event}:{index}"))
                          if event else str(uuid.uuid4()))
            conn.execute(
                "INSERT INTO run_alerts (id, run_id, severity, check_name, message, metric, step, line_number, line, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (anomaly_id, run_id, alert["severity"], alert["check"], alert["message"], alert["metric"], alert["step"],
                 alert["line_number"], alert["line"], now),
            )
            anomaly_refs.append(f"anomaly:{anomaly_id}")
        last_step = max((p["step"] for p in normalized), default=0)
        conn.execute(
            "UPDATE simulation_runs SET updated_at = ?, last_step = MAX(COALESCE(last_step, 0), ?),"
            " watching = CASE WHEN ? THEN 0 ELSE watching END WHERE id = ?",
            (now, last_step, bool(stopped) and not stale, run_id),
        )
        ack = {"points": accepted, "alerts": len(cleaned)}
        if event:
            _save_receipt(conn, producer, event, row["project_id"], member_id, "run.reported", run_id,
                          revision, digest, {"run_id": run_id, "ack": ack, "anomaly_refs": anomaly_refs,
                                             "sample_count": accepted, "stale": stale}, now)
    return ack


def _key(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError(f"{name} must be 1–128 letters, digits, dots, underscores, colons or hyphens.")
    return value


def _points(points: list[dict]) -> list[dict]:
    normalized = []
    for point in points:
        step = point["step"]
        if not isinstance(step, int) or isinstance(step, bool):
            raise ValueError("A sample step must be an integer.")
        values = {}
        for name, value in point["values"].items():
            if not isinstance(name, str) or not name:
                raise ValueError("A measurement needs a name.")
            values[name[:MAX_METRIC_CHARS]] = (value if isinstance(value, (int, float))
                                             and not isinstance(value, bool) and math.isfinite(value) else None)
        normalized.append({"step": step, "values": values})
    return normalized


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _begin(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _active_member(conn: sqlite3.Connection, member_id: str, project_id: str) -> bool:
    return conn.execute("SELECT 1 FROM project_members pm JOIN members m ON m.id = pm.member_id"
                        " WHERE pm.member_id = ? AND pm.project_id = ? AND m.status = 'active'",
                        (member_id, project_id)).fetchone() is not None


def _receipt(conn: sqlite3.Connection, producer: str, event: str, digest: str) -> dict | None:
    row = conn.execute("SELECT payload_hash, payload FROM context_events WHERE producer_id = ? AND event_id = ?",
                       (producer, event)).fetchone()
    if row is None:
        return None
    if row["payload_hash"] != digest:
        raise ReportConflict("That ingestion key was already used for different data.")
    return json.loads(row["payload"])


def _save_receipt(conn: sqlite3.Connection, producer: str, event: str, project_id: str, owner: str,
                  kind: str, run_id: str, revision: int, digest: str, payload: dict, now: str) -> None:
    conn.execute("INSERT INTO context_events (producer_id, event_id, project_id, owner_id, event_type, subject_ref,"
                 " source_revision, payload_hash, payload, state, occurred_at, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?)",
                 (producer, event, project_id, owner, kind, f"run:{run_id}", revision, digest,
                  json.dumps(payload, sort_keys=True, separators=(",", ":")), now, now))


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
