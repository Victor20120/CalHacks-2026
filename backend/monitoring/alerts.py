"""Monitoring alerts from researchers' local simulation watchers.

Session-only: alerts are kept in memory and never written to a database, so
they disappear when the backend restarts. Each researcher only ever sees their
own alerts here. Watched runs, with their alerts, are kept for the researcher
and the group leader in backend/monitoring/runs.py.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from backend.orchestration.evidence_policy import redact_secrets

SEVERITIES = ("informational", "warning", "critical")
CHECKS = ("nan_inf", "overflow", "error_line", "sudden_jump", "worsening", "threshold", "stalled", "missing_output")
MAX_ALERTS_PER_MEMBER = 500
MAX_MESSAGE_CHARS = 500
MAX_LINE_CHARS = 300


class InvalidAlert(Exception):
    pass


@dataclass
class AlertRecord:
    id: str
    member_id: str
    project_id: str
    run_label: str
    severity: str
    check: str
    message: str
    metric: str | None
    line_number: int | None
    line: str
    created_at: str
    step: int | None = None


class AlertStore:
    def __init__(self) -> None:
        self._alerts: dict[str, list[AlertRecord]] = {}
        self._lock = threading.Lock()

    def add(self, member_id: str, project_id: str, run_label: str, alerts: list[dict]) -> list[AlertRecord]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        records = [
            AlertRecord(id=str(uuid.uuid4()), member_id=member_id, project_id=project_id, run_label=run_label[:100],
                        created_at=now, **clean_alert(alert))
            for alert in alerts
        ]
        with self._lock:
            mine = self._alerts.setdefault(member_id, [])
            mine.extend(records)
            del mine[:-MAX_ALERTS_PER_MEMBER]  # keep only the newest
        return records

    def for_member(self, member_id: str, project_id: str | None = None) -> list[dict]:
        """Newest first."""
        with self._lock:
            mine = list(self._alerts.get(member_id, []))
        return [asdict(a) for a in reversed(mine) if project_id is None or a.project_id == project_id]

    def clear(self, member_id: str) -> None:
        with self._lock:
            self._alerts.pop(member_id, None)


def clean_alert(alert: dict) -> dict:
    """A checked copy of an alert from a watcher: known severity and check, sizes
    limited, secrets in the message and log line blanked."""
    if alert.get("severity") not in SEVERITIES:
        raise InvalidAlert(f"severity must be one of {', '.join(SEVERITIES)}.")
    if alert.get("check") not in CHECKS:
        raise InvalidAlert(f"check must be one of {', '.join(CHECKS)}.")
    message, _ = redact_secrets(str(alert.get("message", ""))[:MAX_MESSAGE_CHARS])
    line, _ = redact_secrets(str(alert.get("line") or "")[:MAX_LINE_CHARS])
    return {"severity": alert["severity"], "check": alert["check"], "message": message, "metric": alert.get("metric"),
            "line_number": alert.get("line_number"), "line": line, "step": alert.get("step")}
