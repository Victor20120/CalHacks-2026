"""Saved simulation versions and explicit local build/run execution.

Setup snapshots files and saves the agent's plan. Only run() starts processes.
The snapshot is retained; compilation and execution use another working copy.
"""
from __future__ import annotations

import hashlib
import copy
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.orchestration.evidence_policy import redact_secrets
from backend.orchestration.simulation_setup import InvalidSetup, clean_plan
from local_connector.evidence.project_map import build_project_map
from local_connector.experiments.manifest import DEFAULT_LIMITS, Limits
from local_connector.experiments.sandbox import run_limited
from local_connector.experiments.workspace import copy_project
from local_connector.file_access.grants import Grant
from local_connector.runtime_environment import (
    discover_runtime, missing_runtime_tools, resolve_runtime_command, runtime_environ, runtime_facts,
)

ACTIVE = ("queued", "running")


def machine_facts(runtime: dict | None = None) -> dict:
    return runtime_facts(runtime or discover_runtime(Path.cwd()))


class SimulationNotFound(LookupError):
    pass


class SimulationManager:
    def __init__(self, home: Path | None = None, runner=run_limited, find_program=shutil.which):
        base = Path(os.environ.get("EXPANDRG_HOME") or Path.home() / ".expandrg")
        self.home = Path(home) if home else base / "simulations" / os.environ.get("EXPANDRG_GROUP", "demo")
        self.home.mkdir(parents=True, exist_ok=True)
        self.runner, self.find_program = runner, find_program
        self.lock = threading.RLock()
        self.records: dict[str, dict] = {}
        self.queue: list[tuple[str, object]] = []
        self.cancels: dict[str, threading.Event] = {}
        self.worker = None
        for path in self.home.glob("*/simulation.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self.records[record["id"]] = record
            if record["status"] in ACTIVE:
                record.update(status="interrupted", reason="The local connector restarted during this run.")
                self._save(record)

    def prepare(self, grant: Grant, member_id: str, project_id: str, environment: str | None = None,
                compiler: str | None = None, *, runtime: dict | None = None) -> dict:
        selected_runtime = copy.deepcopy(runtime) if runtime else discover_runtime(
            grant.root, environment, compiler, find_program=self.find_program)
        identifier = uuid.uuid4().hex[:16]
        dest = self.home / identifier / "snapshot"
        try:
            copy_project(grant.root, dest)
            dest.mkdir(parents=True, exist_ok=True)
        except Exception:
            shutil.rmtree(dest.parent, ignore_errors=True)
            raise
        paths = sorted(p for p in dest.rglob("*") if p.is_file())
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.relative_to(dest).as_posix().encode())
            with path.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
        with self.lock:
            version = max((r["version"] for r in self.records.values()
                           if r["member_id"] == member_id and r["project_id"] == project_id), default=0) + 1
            record = {"id": identifier, "version": version, "project_id": project_id, "member_id": member_id,
                      "folder": grant.name, "created_at": _now(), "status": "awaiting_setup", "stage": "",
                      "plan": None, "reason": "", "missing_tools": [], "backend_run_id": None, "setup_job_id": None,
                      "source_version": None, "fingerprint": digest.hexdigest(), "file_count": len(paths),
                      "exit_code": None, "seconds": None, "started_at": None, "finished_at": None,
                      "limits": dict(DEFAULT_LIMITS)}
            record["runtime"] = selected_runtime
            self.records[identifier] = record
            self._save(record)
            return dict(record)

    def context(self, identifier: str, registry) -> dict:
        record = self.get(identifier)
        snapshot = Grant(self.home / identifier / "snapshot", record["folder"], record["created_at"])
        mapped = build_project_map(registry, snapshot)
        return {**mapped.to_payload(), "machine": machine_facts(self._runtime(record))}

    def _runtime(self, record: dict) -> dict:
        # Versions prepared before runtime selection used the connector environment.
        return record.get("runtime") or discover_runtime(self.home / record["id"] / "snapshot", find_program=self.find_program)

    def connect_environment(self, identifier: str, environment: str | None = None, compiler: str | None = None) -> dict:
        with self.lock:
            record = self._get(identifier)
            if record["status"] not in ("awaiting_setup", "needs_input", "ready"):
                raise InvalidSetup("Prepare from this version before changing its environment.")
            old = self._runtime(record)
            inherited = {**os.environ, **old.get("variables", {}),
                         "PATH": old["path"] + os.pathsep + os.environ.get("PATH", "")}
            selected = discover_runtime(Path(old["source_root"]), environment or old["python"], compiler,
                                        environ=inherited, find_program=self.find_program)
            if not environment:
                selected.update(label=old["label"], kind=old["kind"])
            record["runtime"] = selected
            self._save(record)
            return self.set_plan(identifier, record["plan"]) if record["plan"] else dict(record)

    def set_plan(self, identifier: str, plan: dict) -> dict:
        with self.lock:
            record = self._get(identifier)
            if record["status"] not in ("awaiting_setup", "needs_input", "ready"):
                raise InvalidSetup("This version already ran. Prepare another version to change its setup.")
            files = [p.relative_to(self.home / identifier / "snapshot").as_posix()
                     for p in (self.home / identifier / "snapshot").rglob("*") if p.is_file()]
            checked = clean_plan(plan, files)
            missing = self._missing(checked, record)
            record.update(plan=checked, missing_tools=missing, setup_job_id=None, reason="",
                          status="needs_input" if checked["questions"] or missing or not checked["run_command"] else "ready")
            self._save(record)
            return dict(record)

    def planning(self, identifier: str, job_id: str) -> dict:
        with self.lock:
            record = self._get(identifier)
            if record["status"] not in ("awaiting_setup", "needs_input", "ready"):
                raise InvalidSetup("This version already ran.")
            record.update(status="awaiting_setup", setup_job_id=job_id)
            self._save(record)
            return dict(record)

    def setup_failed(self, identifier: str, error: str) -> dict:
        with self.lock:
            record = self._get(identifier)
            if record["status"] in ("awaiting_setup", "needs_input", "ready"):
                record.update(status="needs_input", setup_job_id=None, reason=redact_secrets(error)[0][:1000])
                self._save(record)
            return dict(record)

    def _missing(self, plan: dict, record: dict) -> list[str]:
        return missing_runtime_tools(plan, self._runtime(record), self.find_program)

    def run(self, identifier: str, monitor_factory) -> dict:
        with self.lock:
            record = self._get(identifier)
            if record["status"] in ACTIVE:
                return dict(record)  # a double click never starts another process
            if record["status"] != "ready":
                raise InvalidSetup("Prepare a complete setup before clicking Run.")
            missing = self._missing(record["plan"], record)
            if missing:
                record.update(missing_tools=missing, status="needs_input")
                self._save(record)
                raise InvalidSetup("These tools are not connected in the selected environment: " + ", ".join(missing))
            monitor, backend_run_id = monitor_factory(dict(record))
            record.update(status="queued", backend_run_id=backend_run_id)
            self._save(record)
            self.queue.append((identifier, monitor))
            if not self.worker or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._work, name="simulations", daemon=True)
                self.worker.start()
            return dict(record)

    def repeat(self, identifier: str) -> dict:
        old = self.get(identifier)
        snapshot = self.home / identifier / "snapshot"
        record = self.prepare(Grant(snapshot, old["folder"], old["created_at"]), old["member_id"], old["project_id"],
                              runtime=self._runtime(old))
        with self.lock:
            self.records[record["id"]]["source_version"] = old["version"]
            self._save(self.records[record["id"]])
        return self.set_plan(record["id"], old["plan"]) if old["plan"] else self.get(record["id"])

    def stop(self, identifier: str) -> dict:
        with self.lock:
            record = self._get(identifier)
            if identifier in self.cancels:
                self.cancels[identifier].set()
            elif record["status"] == "queued":
                for index, (queued_id, monitor) in enumerate(self.queue):
                    if queued_id == identifier:
                        self.queue.pop(index)
                        monitor.end(None)
                        break
                record.update(status="stopped", reason="Stopped before execution.", finished_at=_now())
                self._save(record)
            return dict(record)

    def _work(self) -> None:
        while True:
            with self.lock:
                if not self.queue:
                    return
                identifier, monitor = self.queue.pop(0)
                record = self._get(identifier)
                record.update(status="running", started_at=_now())
                cancel = self.cancels[identifier] = threading.Event()
                self._save(record)
            folder = self.home / identifier
            started = time.monotonic()
            status, reason, exit_code = "failed", "", None
            try:
                copy_project(folder / "snapshot", folder / "work")
                cwd = (folder / "work" / record["plan"]["working_directory"]).resolve()
                if not cwd.is_relative_to((folder / "work").resolve()) or not cwd.is_dir():
                    raise InvalidSetup("The setup's working directory does not exist in this version.")
                commands = [*record["plan"]["build_commands"], record["plan"]["run_command"]]
                for index, command in enumerate(commands):
                    if cancel.is_set():
                        status, reason = "stopped", "Stopped by you."
                        break
                    with self.lock:
                        record["stage"] = "Running simulation" if index == len(commands) - 1 else f"Build step {index + 1}"
                        self._save(record)
                    remaining = record["limits"]["wall_seconds"] - int(time.monotonic() - started)
                    if remaining <= 0:
                        status, reason = "stopped", "The run reached its time limit."
                        break
                    stage_log = folder / f"stage-{index}.log"
                    tail_done = threading.Event()
                    tail = threading.Thread(target=_tail, args=(stage_log, folder / "output.log", monitor, tail_done), daemon=True)
                    tail.start()
                    try:
                        limits = Limits(**{**record["limits"], "wall_seconds": remaining})
                        runtime = self._runtime(record)
                        resolved = resolve_runtime_command(command, cwd, folder / "work", runtime, self.find_program)
                        outcome = self.runner(resolved, cwd, stage_log, limits, cancel, environment=runtime_environ(runtime))
                    finally:
                        tail_done.set()
                        tail.join(5)
                    status, reason, exit_code = outcome.status, outcome.reason, outcome.exit_code
                    if status != "finished":
                        reason = f"{record['stage']}: {reason}"
                        break  # a failed compilation never launches the executable
            except Exception as exc:
                status, reason = "failed", redact_secrets(str(exc))[0][:1000]
            finally:
                try:
                    monitor.end(exit_code if status != "failed" or exit_code else 1)
                except Exception:
                    pass  # a reporting outage must not leave execution marked as running
                with self.lock:
                    record.update(status=status, reason=reason, exit_code=exit_code,
                                  seconds=round(time.monotonic() - started, 2), finished_at=_now())
                    self.cancels.pop(identifier, None)
                    self._save(record)

    def get(self, identifier: str) -> dict:
        with self.lock:
            return dict(self._get(identifier))

    def detail(self, identifier: str) -> dict:
        record = self.get(identifier)
        log = self.home / identifier / "output.log"
        output = ""
        if log.exists():
            with log.open("rb") as f:
                f.seek(max(0, log.stat().st_size - 12000))
                output = redact_secrets(f.read().decode("utf-8", errors="replace"))[0]
        return {**record, "output": output}

    def list(self, member_id: str, project_id: str) -> list[dict]:
        with self.lock:
            return sorted([dict(r) for r in self.records.values()
                           if r["member_id"] == member_id and r["project_id"] == project_id],
                          key=lambda r: r["version"], reverse=True)

    def wait(self, timeout=60):
        if self.worker:
            self.worker.join(timeout)

    def _get(self, identifier: str) -> dict:
        if identifier not in self.records:
            raise SimulationNotFound("No saved simulation with that ID.")
        return self.records[identifier]

    def _save(self, record: dict) -> None:
        path = self.home / record["id"] / "simulation.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temp.replace(path)


def _tail(stage: Path, combined: Path, monitor, done: threading.Event) -> None:
    position, waiting = 0, b""
    while True:
        if stage.exists():
            with stage.open("rb") as f:
                f.seek(position)
                chunk = f.read(65536)
            if chunk:
                position += len(chunk)
                with combined.open("ab") as out:
                    out.write(chunk)
                parts = (waiting + chunk).split(b"\n")
                waiting = parts.pop()[-8000:]
                monitor.feed([line.decode("utf-8", errors="replace") for line in parts])
                continue
        if done.wait(.1):
            # Check once more after the process closed its output file.
            if stage.exists() and stage.stat().st_size > position:
                continue
            if waiting:
                monitor.feed([waiting.decode("utf-8", errors="replace")])
            return


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
