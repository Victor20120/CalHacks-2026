"""Experiments: prepared in a copy of the project, run only when the researcher
says so, then compared with the original run.

    prepare()  copy the project and apply the change. Never runs anything.
    run(ids)   the researcher's Run / Run all: queue them; they run one at a time.
    stop(id)   the stop button.

Everything stays on this computer, under $EXPANDRG_HOME/experiments/<id>/:
experiment.json (the record), project/ (the copy) and output.log.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from local_connector.experiments.manifest import EntryPoint, Limits, load_manifest
from local_connector.experiments.sandbox import resolve_command, run_limited
from local_connector.experiments.workspace import ExperimentRefused, apply_changes, check_changes, copy_project
from local_connector.simulation_monitor.checks import sample_of, scan_lines

MAX_LOG_LINES = 200_000
WAITING = ("prepared", "queued", "running")


class ExperimentNotFound(LookupError):
    pass


def default_home() -> Path:
    return Path(os.environ.get("EXPANDRG_HOME") or Path.home() / ".expandrg") / "experiments"


class ExperimentManager:
    def __init__(self, home: Path | None = None, runner=run_limited):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self._run = runner
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._cancel: dict[str, threading.Event] = {}
        self._worker: threading.Thread | None = None
        self._records = {r["id"]: r for r in self._load()}

    # --- preparing ------------------------------------------------------------

    def prepare(self, root: Path, project_name: str, member_id: str, project_id: str, entry_name: str,
                changes: dict, set_id: str | None = None, baseline: str | None = None, label: str = "") -> dict:
        """Copy the project and apply the change. Nothing runs."""
        entry = self._entry(root, entry_name)
        checked = check_changes(root, entry, changes)
        experiment_id = uuid.uuid4().hex[:12]
        folder = self.home / experiment_id
        try:
            copy_project(Path(root), folder / "project")
            apply_changes(folder / "project", entry, checked)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        record = {
            "id": experiment_id, "set_id": set_id or experiment_id, "member_id": member_id, "project_id": project_id,
            "root": str(root), "project_name": project_name, "entry": entry.name, "command": list(entry.command),
            "limits": vars(entry.limits), "changes": checked, "label": label[:120] or _describe(checked),
            "baseline": baseline, "status": "prepared", "reason": "", "exit_code": None, "seconds": None,
            "created_at": _now(), "started_at": None, "finished_at": None,
        }
        with self._lock:
            self._records[experiment_id] = record
            self._save(record)
        return dict(record)

    def entry_points(self, root: Path) -> dict[str, EntryPoint]:
        return load_manifest(Path(root))

    # --- running --------------------------------------------------------------

    def run(self, ids: list[str]) -> list[dict]:
        """The researcher's Run / Run all. Only prepared experiments are queued."""
        with self._lock:
            records = [self._get(i) for i in ids]
            for record in records:
                if record["status"] == "prepared":
                    record["status"] = "queued"
                    self._save(record)
                    self._queue.append(record["id"])
            if self._queue and not (self._worker and self._worker.is_alive()):
                self._worker = threading.Thread(target=self._work, name="experiments", daemon=True)
                self._worker.start()
            return [dict(r) for r in records]

    def stop(self, experiment_id: str) -> dict:
        with self._lock:
            record = self._get(experiment_id)
            if record["status"] == "queued":
                self._queue.remove(experiment_id)
                self._finish(record, "stopped", "Stopped by you before it started.", None, None)
            elif record["status"] == "running":
                self._cancel[experiment_id].set()
            return dict(record)

    def wait(self, timeout: float = 60.0) -> None:
        """Wait for the queue to empty (for tests and the command line)."""
        worker = self._worker
        if worker:
            worker.join(timeout)

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                record = self._records[self._queue.pop(0)]
                record.update(status="running", started_at=_now())
                cancel = self._cancel[record["id"]] = threading.Event()
                self._save(record)
            folder = self.home / record["id"]
            copy_root = folder / "project"
            try:
                outcome = self._run(resolve_command(tuple(record["command"]), copy_root), copy_root,
                                    folder / "output.log", Limits(**record["limits"]), cancel)
                result = (outcome.status, outcome.reason, outcome.exit_code, outcome.seconds)
            except Exception as exc:  # never leave an experiment stuck as running
                result = ("failed", f"Could not run: {exc}", None, None)
            with self._lock:
                self._cancel.pop(record["id"], None)
                self._finish(record, *result)

    def _finish(self, record: dict, status: str, reason: str, exit_code, seconds) -> None:
        record.update(status=status, reason=reason, exit_code=exit_code, seconds=seconds, finished_at=_now())
        self._save(record)

    # --- reading --------------------------------------------------------------

    def list(self, member_id: str | None = None, project_id: str | None = None) -> list[dict]:
        with self._lock:
            records = [dict(r) for r in self._records.values()
                       if (member_id is None or r["member_id"] == member_id)
                       and (project_id is None or r["project_id"] == project_id)]
        return sorted(records, key=lambda r: r["created_at"], reverse=True)

    def detail(self, experiment_id: str) -> dict:
        """The record with what its log shows: numbers per step, alerts and a summary."""
        with self._lock:
            record = dict(self._get(experiment_id))
        log = self.home / experiment_id / "output.log"
        lines = _read_lines(log) if log.is_file() else []
        return {**record, **summarize(lines)}

    def delete(self, experiment_id: str) -> None:
        with self._lock:
            record = self._get(experiment_id)
            if record["status"] in ("queued", "running"):
                raise ExperimentRefused("Stop it before deleting it.")
            del self._records[experiment_id]
        shutil.rmtree(self.home / experiment_id, ignore_errors=True)

    # --- storage --------------------------------------------------------------

    def _entry(self, root: Path, name: str) -> EntryPoint:
        entries = load_manifest(Path(root))
        if name not in entries:
            raise ExperimentRefused(f'There is no entry point "{name}" in expandrg.json.')
        return entries[name]

    def _get(self, experiment_id: str) -> dict:
        record = self._records.get(experiment_id)
        if record is None:
            raise ExperimentNotFound("No experiment with that ID.")
        return record

    def _save(self, record: dict) -> None:
        path = self.home / record["id"] / "experiment.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    def _load(self) -> list[dict]:
        records = []
        for path in self.home.glob("*/experiment.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("status") in ("queued", "running"):  # the connector stopped while it waited or ran
                record.update(status="stopped", reason="Stopped: the local connector was restarted.", finished_at=_now())
                path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            records.append(record)
        return records


def summarize(lines: list[str]) -> dict:
    """Numbers per step (for graphs), alerts, and the last step reached."""
    series: dict[str, list[list]] = {}
    for line in lines:
        sample = sample_of(line)
        if sample:
            for name, value in sample.values.items():
                series.setdefault(name, []).append([sample.step, value])
    alerts = [a.to_dict() for a in scan_lines(lines)]
    steps = [p[0] for points in series.values() for p in points]
    return {"metrics": list(series), "series": series, "alerts": alerts, "last_step": max(steps, default=None),
            "lines": len(lines)}


def _read_lines(path: Path) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\n") for _, line in zip(range(MAX_LOG_LINES), f)]


def _describe(changes: dict) -> str:
    return ", ".join(f"{k} = {new}" for k, (_, new) in changes.items()) or "No changes (the original again)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
