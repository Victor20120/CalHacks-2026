"""The datasets a viewer may graph, as one catalog: their watched runs (shared database, the
existing permission checks) and their experiments with each set's original run (the local
connector). Every dataset has numbers per step for its measures; non-finite values are gaps.

Catalog IDs: "run:<run id>", "exp:<experiment id>", "base:<experiment set id>" (the original
run an experiment set was compared with).
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from typing import Any

from backend.monitoring.runs import MAX_POINTS_PER_METRIC, run_detail, visible_runs
from backend.orchestration.toolkit import ToolError

MAX_EXPERIMENTS = 30
DONE = ("finished", "failed", "stopped", "running")  # experiments with output to graph


class DatasetNotFound(LookupError):
    pass


class Sources:
    """What one viewer may graph in one project. The connector is asked once, when it's made."""

    def __init__(self, conn: sqlite3.Connection, viewer: dict, project_id: str, connector: Any):
        self.conn, self.viewer, self.project_id, self.connector = conn, viewer, project_id, connector
        self.notes: list[str] = []
        try:
            self._experiments = _experiments(viewer, project_id, connector)
        except ToolError as exc:
            self.notes.append(f"Experiments aren't listed: {exc}")
            self._experiments = []
        self.items = self._catalog()

    def _catalog(self) -> list[dict]:
        items = []
        for r in visible_runs(self.conn, self.viewer, [self.project_id]):
            if not r["metrics"]:
                continue  # a shared log with no numbers per step: nothing to graph
            items.append({
                "id": f"run:{r['id']}", "source": "run", "label": r["run_label"], "researcher": r["member_name"],
                "started": r["started_at"], "metrics": r["metrics"], "last_step": r["last_step"],
                "alerts": {"critical": r["critical"], "warning": r["warning"]}, "parameters": {},
            })
        originals = set()
        for d in self._experiments:
            if d.get("metrics"):
                items.append({
                    "id": f"exp:{d['id']}", "source": "experiment", "label": d["label"], "researcher": self.viewer["display_name"],
                    "started": d.get("started_at") or d["created_at"], "metrics": d["metrics"], "last_step": d.get("last_step"),
                    "alerts": _alert_counts(d.get("alerts", [])), "parameters": _parameters(d, 1), "status": d["status"],
                })
            base = d.get("baseline_result")
            if base and base.get("metrics") and d["set_id"] not in originals:
                originals.add(d["set_id"])
                items.append({
                    "id": f"base:{d['set_id']}", "source": "original", "label": f"Original ({d['baseline']})",
                    "researcher": self.viewer["display_name"], "started": d["created_at"], "metrics": base["metrics"],
                    "last_step": base.get("last_step"), "alerts": _alert_counts(base.get("alerts", [])),
                    "parameters": _parameters(d, 0),
                })
        return items

    def load(self, dataset_id: str, full: bool = False) -> dict:
        """One dataset's numbers: {"id", "label", "source", "parameters", "series": {measure: [(step, value)]},
        "alerts", "thinned": [measures with points dropped]}. DatasetNotFound for anything the viewer
        may not see, or that isn't in this project."""
        kind, _, key = dataset_id.partition(":")
        if kind == "run":
            detail = run_detail(self.conn, self.viewer, key)
            if detail is None or detail["project_id"] != self.project_id:
                raise DatasetNotFound(f"No run {dataset_id} in this project.")
            series = {m: _clean(points) for m, points in detail["series"].items()}
            local = self.local(detail, full)
            if full and local.get("series"):
                expanded = {m: _clean(points) for m, points in local["series"].items()}
                indexed = {m: dict(points) for m, points in expanded.items()}
                matches = all(m in indexed and all(step in indexed[m] and
                              ((value is None and indexed[m][step] is None) or
                               (value is not None and indexed[m][step] is not None and
                                math.isclose(value, indexed[m][step], rel_tol=1e-9, abs_tol=0)))
                              for step, value in points) for m, points in series.items())
                if matches:
                    series = expanded
                else:
                    self.notes.append(f"The original log no longer matches {detail['run_label']}; recorded data is retained.")
            return {"id": dataset_id, "label": detail["run_label"], "source": "run", "parameters": {}, "series": series,
                    "alerts": detail.get("alerts", []), "units": local.get("units", {}),
                    "thinned": [] if full and local.get("series") and matches else [m for m, p in series.items() if _thinned(p)]}
        for d in self._experiments:
            if kind == "exp" and d["id"] == key:
                return {"id": dataset_id, "label": d["label"], "source": "experiment", "parameters": _parameters(d, 1),
                        "series": {m: _clean(p) for m, p in (d.get("series") or {}).items()},
                        "alerts": d.get("alerts", []), "units": d.get("units", {}), "thinned": []}
            base = d.get("baseline_result")
            if kind == "base" and d["set_id"] == key and base:
                return {"id": dataset_id, "label": f"Original ({d['baseline']})", "source": "original",
                        "parameters": _parameters(d, 0), "series": {m: _clean(p) for m, p in (base.get("series") or {}).items()},
                        "alerts": base.get("alerts", []), "units": d.get("units", {}), "thinned": []}
        raise DatasetNotFound(f"No dataset {dataset_id} in this project.")


    def local(self, run: dict, full: bool = False) -> dict:
        try:
            answer = self.connector.get("/visualize/local-data", {
                "run_id": run["id"], "run_label": run["run_label"], "member_id": run["member_id"],
                "project_id": self.project_id, "full": full})
            return answer if isinstance(answer, dict) else {}
        except ToolError as exc:
            if full:
                self.notes.append(f"Full-resolution data unavailable for {run['run_label']}: {exc}")
            return {}


def fingerprint(points: list[tuple]) -> str:
    """Changes when the data does: its length, last step and values."""
    digest = hashlib.sha256(repr([(s, v) for s, v in points]).encode()).hexdigest()
    return f"{len(points)}:{points[-1][0] if points else ''}:{digest[:16]}"


def _experiments(viewer: dict, project_id: str, connector: Any) -> list[dict]:
    """The viewer's experiments in the project that have output, newest first, with their numbers."""
    records = connector.get("/experiments", {"member_id": viewer["id"], "project_id": project_id})
    details = []
    for record in records[:MAX_EXPERIMENTS]:
        if record.get("member_id") != viewer["id"] or record.get("project_id") != project_id or record.get("status") not in DONE:
            continue  # the connector already filters; checked again here
        details.append(connector.get(f"/experiments/{record['id']}"))
    return details


def _parameters(detail: dict, which: int) -> dict:
    """The changed parameters' new (which=1) or original (which=0) values."""
    return {k: v[which] for k, v in (detail.get("changes") or {}).items()}


def _clean(points: list) -> list[tuple]:
    """(step, value) sorted by step, one per step (the last reported wins); NaN and Inf become gaps."""
    by_step: dict[int, float | None] = {}
    for step, value in points:
        if isinstance(step, bool) or not isinstance(step, (int, float)):
            continue
        number = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
        by_step[int(step)] = number
    return sorted(by_step.items())


def _thinned(points: list[tuple]) -> bool:
    """A long watched run whose spacing grew: the monitor dropped every other point past its limit."""
    if len(points) < MAX_POINTS_PER_METRIC // 2:
        return False
    gaps = [b[0] - a[0] for a, b in zip(points, points[1:])]
    return bool(gaps) and max(gaps) >= 2 * min(gaps)


def _alert_counts(alerts: list[dict]) -> dict:
    return {s: sum(1 for a in alerts if a.get("severity") == s) for s in ("critical", "warning")}
