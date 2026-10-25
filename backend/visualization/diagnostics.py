"""Diagnostic graph defaults are deterministic and obey existing run visibility."""
from __future__ import annotations

from backend.monitoring.runs import run_detail, visible_runs
from backend.visualization.spec import assign_colors, new_spec


def diagnostic_spec(conn, member: dict, project_id: str, run_id: str, alert_id: str | None) -> dict:
    run = run_detail(conn, member, run_id)
    if run is None or run["project_id"] != project_id:
        raise ValueError("That run is not available in this project.")
    alert = next((a for a in run["alerts"] if a["id"] == alert_id), None) if alert_id else next(
        (a for a in reversed(run["alerts"]) if a["severity"] == "critical"), None)
    if alert_id and alert is None:
        raise ValueError("That problem is not part of this run.")
    metric = alert.get("metric") if alert else None
    metric = metric if metric in run["metrics"] else next(iter(run["metrics"]), None)
    if metric is None:
        raise ValueError("This run has no recorded measurements to diagnose.")
    spec = new_spec(project_id)
    spec.update(datasets=["run:" + run_id], metrics=[metric])
    clean = next((r for r in visible_runs(conn, member, [project_id])
                  if r["id"] != run_id and r["started_at"] <= run["started_at"]
                  and not r["critical"] and not r["warning"] and metric in r["metrics"]), None)
    if clean:
        spec["datasets"].append("run:" + clean["id"])
    step = alert.get("step") if alert else None
    if step is not None:
        window = max(20, abs(step) * .2)
        spec["range"] = {"from": max(0, step - window), "to": step + window}
    values = [value for _, value in run["series"].get(metric, []) if value is not None]
    if (alert and alert["severity"] == "critical" and alert.get("check") in ("nan_inf", "overflow")
            and any(value > 0 for value in values) and all(value >= 0 for value in values)):
        spec["y_scale"] = "log"
    spec["style"]["title"] = "Diagnosis: " + run["run_label"][:100]
    assign_colors(spec)
    return spec
