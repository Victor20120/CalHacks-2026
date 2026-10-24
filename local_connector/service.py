"""The local connector as a small service for the dashboard, on this computer only.

A browser can't read local files, so the dashboard asks this service, which runs
on the researcher's computer, to list granted folders and their files, extract
evidence, watch run logs and run experiments. It enforces the same folder grants
and evidence rules as the command line. It never starts, pauses or stops the
researcher's own simulations; experiments run only in copies, and only when the
researcher clicks Run.

    python -m local_connector serve        (listens on 127.0.0.1:8001)
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Callable

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.orchestration.evidence_policy import MAX_ITEMS, redact_secrets
from local_connector.evidence.extract import extract_evidence, file_kind
from local_connector.evidence.project_map import build_project_map
from local_connector.experiments.manager import ExperimentManager, ExperimentNotFound, summarize
from local_connector.experiments.manifest import ManifestError
from local_connector.experiments.workspace import ExperimentRefused, read_parameters
from local_connector.file_access.grants import AccessDenied, Grant, GrantRegistry
from local_connector.file_access.walk import iter_project_files
from local_connector.simulation_monitor.checks import Alert, Sample
from local_connector.simulation_monitor.stream import StreamWatch
from local_connector.simulation_monitor.watcher import LogWatcher

log = logging.getLogger(__name__)

DEFAULT_BACKEND = "http://127.0.0.1:8000"
MAX_LISTED_FILES = 500
MAX_REPORT_POINTS = 2_000  # per request to the backend
MAX_REPORT_ALERTS = 50
MAX_PENDING_POINTS = 5_000  # waiting while the backend can't be reached; the oldest go first
MAX_KEPT_STREAMS = 50  # finished streamed runs listed; older ones are forgotten (the backend keeps them)


class RootIn(BaseModel):
    root: str = Field(min_length=1, max_length=1000)


class EvidenceIn(BaseModel):
    root: str = Field(min_length=1, max_length=1000)
    selections: list[str] = Field(min_length=1, max_length=MAX_ITEMS)


class WatchIn(BaseModel):
    root: str = Field(min_length=1, max_length=1000)
    file: str = Field(min_length=1, max_length=500)
    project_id: str = Field(min_length=1, max_length=64)
    member_id: str = Field(min_length=1, max_length=64)


class StreamIn(BaseModel):
    root: str = Field(min_length=1, max_length=1000)  # the VS Code workspace folder; must be granted
    project_id: str = Field(min_length=1, max_length=64)
    member_id: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=1000)  # what was run; becomes the run's name


class LinesIn(BaseModel):
    lines: list[str] = Field(max_length=5000)


class EndIn(BaseModel):
    exit_code: int | None = None


class ExperimentIn(BaseModel):
    root: str = Field(min_length=1, max_length=1000)
    project_id: str = Field(min_length=1, max_length=64)
    member_id: str = Field(min_length=1, max_length=64)
    entry: str = Field(min_length=1, max_length=40)
    changes: dict[str, bool | int | float | str] = Field(default_factory=dict, max_length=20)
    set_id: str | None = Field(default=None, max_length=40)
    baseline: str | None = Field(default=None, max_length=500)  # a log in the project to compare with
    label: str = Field(default="", max_length=120)


class RunExperimentsIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=20)


class BackendPoster:
    """Sends a watched run to the backend as the researcher (prototype sign-in), so it
    shows live on the dashboard and can be opened again later: the numbers read from
    each step of the log (for graphs) and the alerts, never the log file itself.
    What couldn't be delivered waits for the next try; undelivered alerts are counted
    in the watch status."""

    def __init__(self, backend_url: str, member_id: str, project_id: str, run_label: str, http: Any):
        self.backend_url, self.member_id, self.project_id, self.run_label = backend_url, member_id, project_id, run_label
        self.http = http
        self.run_id: str | None = None  # the run on the dashboard, once the backend has it
        self.undelivered = 0
        self._token: str | None = None
        self._points: list[dict] = []
        self._alerts: list[dict] = []

    def __call__(self, alerts: list[Alert]) -> None:
        body = {"project_id": self.project_id, "run_label": self.run_label, "alerts": [a.to_dict() for a in alerts]}
        try:
            self._post("/alerts", body)
        except Exception:
            self.undelivered += len(alerts)
            log.warning("Could not send %d alert(s) to the backend", len(alerts))
        self._alerts = (self._alerts + [a.to_dict() for a in alerts])[-MAX_REPORT_ALERTS * 4:]
        self._report()

    def start(self) -> None:
        """Put the run on the dashboard right away, before the log has any numbers."""
        self._report()

    def samples(self, samples: list[Sample]) -> None:
        """Step numbers from the watcher."""
        self._points = (self._points + [s.to_dict() for s in samples])[-MAX_PENDING_POINTS:]
        self._report()

    def stopped(self) -> None:
        """Marks the run as no longer watched (the simulation itself is never touched)."""
        self._report(stopped=True)

    def new_run(self) -> None:
        """The log was replaced by a new run: close this run; the next numbers start another."""
        if self.run_id:
            self._report(stopped=True)
            self.run_id, self._points, self._alerts = None, [], []

    def _report(self, stopped: bool = False) -> None:
        try:
            if self.run_id is None:
                run = self._post("/simulation-runs", {"project_id": self.project_id, "run_label": self.run_label})
                self.run_id = run["id"]
            while True:
                points, alerts = self._points[:MAX_REPORT_POINTS], self._alerts[:MAX_REPORT_ALERTS]
                last = len(points) == len(self._points) and len(alerts) == len(self._alerts)
                self._post(f"/simulation-runs/{self.run_id}/report",
                           {"points": points, "alerts": alerts, "stopped": stopped and last})
                del self._points[: len(points)], self._alerts[: len(alerts)]
                if last:
                    return
        except Exception:
            log.warning("Could not send the run's numbers to the backend; will try again")

    def _post(self, path: str, body: dict) -> Any:
        if self._token is None:
            signed_in = self.http.post(f"{self.backend_url}/sign-in", json={"member_id": self.member_id}, timeout=10)
            signed_in.raise_for_status()
            self._token = signed_in.json()["token"]
        sent = self.http.post(f"{self.backend_url}{path}", json=body, timeout=10,
                              headers={"Authorization": f"Bearer {self._token}"})
        if sent.status_code == 401:  # the backend restarted; sign in again next time
            self._token = None
        sent.raise_for_status()
        return sent.json()


def list_files(grant: Grant) -> tuple[list[dict], bool]:
    """Files the dashboard may offer for selection: no sensitive or binary files,
    nothing inside .git, virtual environments or similar folders. Returns (files, truncated)."""
    files: list[dict] = []
    for relative, full in iter_project_files(grant):
        files.append({"path": relative, "size": full.stat().st_size, "kind": file_kind(relative)})
        if len(files) >= MAX_LISTED_FILES:
            return files, True
    return files, False


def create_service(
    registry: GrantRegistry | None = None,
    backend_url: str | None = None,
    http: Any = None,
    watcher_factory: Callable[..., LogWatcher] = LogWatcher,
    experiments: ExperimentManager | None = None,
) -> FastAPI:
    app = FastAPI(title="ExpandRG local connector", version="0.1.0")
    registry = registry or GrantRegistry()
    backend = (backend_url or os.environ.get("EXPANDRG_BACKEND") or DEFAULT_BACKEND).rstrip("/")
    http = http or requests.Session()
    watches: dict[str, dict] = {}
    lock = threading.Lock()

    def grant_for(root: str) -> Grant:
        try:
            return registry.grant_for_root(root)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/grants")
    def grants() -> list[dict]:
        return [{"root": str(g.root), "name": g.name, "granted_at": g.granted_at} for g in registry.grants()]

    @app.post("/grants")
    def grant(body: RootIn) -> dict:
        try:
            g = registry.grant(body.root)
        except AccessDenied as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"root": str(g.root), "name": g.name, "granted_at": g.granted_at}

    @app.post("/files")
    def files(body: RootIn) -> dict:
        found, truncated = list_files(grant_for(body.root))
        return {"files": found, "truncated": truncated}

    @app.post("/evidence")
    def evidence(body: EvidenceIn) -> dict:
        try:
            ev = extract_evidence(registry, grant_for(body.root), body.selections)
        except (AccessDenied, ValueError) as exc:
            raise HTTPException(status_code=403 if isinstance(exc, AccessDenied) else 422, detail=str(exc)) from None
        return {"project": ev.project, "evidence": ev.to_payload(), "total_chars": ev.total_chars,
                "redactions": ev.redactions, "skipped": ev.skipped}

    @app.post("/project-map")
    def project_map(body: RootIn) -> dict:
        """A map of the whole granted folder for a project overview: the tree and the start of key files."""
        m = build_project_map(registry, grant_for(body.root))
        return {"project": m.project, **m.to_payload(), "total_chars": m.total_chars,
                "redactions": m.redactions, "left_out": m.left_out}

    @app.post("/watches")
    def start_watch(body: WatchIn) -> dict:
        g = grant_for(body.root)
        try:
            resolved, relative = registry.check_path(body.file, g)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        poster = BackendPoster(backend, body.member_id, body.project_id, relative, http)
        watcher = watcher_factory(resolved, poster)
        watcher.on_samples, watcher.on_restart = poster.samples, poster.new_run
        poster.start()
        watch_id = str(uuid.uuid4())
        with lock:
            watches[watch_id] = {"watcher": watcher, "poster": poster, "root": str(g.root), "file": relative,
                                 "project_id": body.project_id, "member_id": body.member_id}
        watcher.start()
        return {"id": watch_id, **_status(watch_id, watches[watch_id])}

    @app.get("/watches")
    def list_watches() -> list[dict]:
        with lock:
            return [{"id": wid, **_status(wid, w)} for wid, w in watches.items()]

    @app.delete("/watches/{watch_id}")
    def stop_watch(watch_id: str) -> dict:
        """Stops watching. The simulation itself is never touched."""
        with lock:
            w = watches.pop(watch_id, None)
        if w is None:
            raise HTTPException(status_code=404, detail="No watch with that ID.")
        w["watcher"].stop()
        w["poster"].stopped()
        return {"status": "stopped", "detail": "Stopped watching. The simulation was not touched."}

    # --- Streamed runs: a VS Code terminal's output, line by line, instead of a log file ---

    streams: dict[str, dict] = {}

    def stream_for(stream_id: str) -> dict:
        with lock:
            stream = streams.get(stream_id)
        if stream is None:
            raise HTTPException(status_code=404, detail="No streamed run with that ID.")
        return stream

    def stream_status(stream_id: str, s: dict) -> dict:
        return {"id": stream_id, "label": s["label"], "project_id": s["project_id"], "member_id": s["member_id"],
                "run_id": s["poster"].run_id, "started_at": s["started_at"], **s["watch"].status()}

    @app.post("/streams")
    def start_stream(body: StreamIn) -> dict:
        """A run the researcher started in a VS Code terminal of a granted folder. Its output is
        sent here line by line; it is never paused or stopped."""
        grant_for(body.root)
        label, _ = redact_secrets(" ".join(body.command.split()))
        label = label if len(label) <= 100 else label[:97] + "..."
        poster = BackendPoster(backend, body.member_id, body.project_id, label, http)
        poster.start()
        stream_id = str(uuid.uuid4())
        with lock:
            streams[stream_id] = {"watch": StreamWatch(poster), "poster": poster, "label": label,
                                  "project_id": body.project_id, "member_id": body.member_id,
                                  "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            finished = [k for k, v in streams.items() if v["watch"].state != "watching"]
            for old in finished[: max(0, len(streams) - MAX_KEPT_STREAMS)]:
                del streams[old]
        return stream_status(stream_id, streams[stream_id])

    @app.post("/streams/{stream_id}/lines")
    def stream_lines(stream_id: str, body: LinesIn) -> dict:
        s = stream_for(stream_id)
        new = s["watch"].feed(body.lines)
        return {**stream_status(stream_id, s), "new_alerts": [a.to_dict() for a in new]}

    @app.post("/streams/{stream_id}/end")
    def end_stream(stream_id: str, body: EndIn) -> dict:
        s = stream_for(stream_id)
        s["watch"].end(body.exit_code)
        return stream_status(stream_id, s)

    @app.get("/streams")
    def list_streams(member_id: str | None = None) -> list[dict]:
        with lock:
            items = list(streams.items())
        return [stream_status(k, v) for k, v in items if member_id is None or v["member_id"] == member_id]

    # --- Experiments: prepared in a copy, run only on the researcher's click ---

    lab = experiments or ExperimentManager()

    def refused(exc: Exception) -> HTTPException:
        if isinstance(exc, AccessDenied):
            return HTTPException(status_code=403, detail=str(exc))
        if isinstance(exc, ExperimentNotFound):
            return HTTPException(status_code=404, detail=str(exc))
        return HTTPException(status_code=422, detail=str(exc))

    @app.post("/manifest")
    def manifest(body: RootIn) -> dict:
        """What experiments may run in this project (expandrg.json), the current values
        of the parameters they may change, and the logs they can be compared with."""
        g = grant_for(body.root)
        try:
            entries = lab.entry_points(g.root)
            points = [{**e.to_dict(), "values": read_parameters(g.root, e)} for e in entries.values()]
        except (ManifestError, ExperimentRefused) as exc:
            raise refused(exc) from None
        logs = [f["path"] for f in list_files(g)[0] if f["kind"] == "log"]
        return {"project": g.name, "entry_points": points, "logs": logs}

    @app.post("/experiments", status_code=201)
    def prepare_experiment(body: ExperimentIn) -> dict:
        """Copy the project and apply the change. Nothing runs until POST /experiments/run."""
        g = grant_for(body.root)
        try:
            baseline = registry.check_path(body.baseline, g)[1] if body.baseline else None
            return lab.prepare(g.root, g.name, body.member_id, body.project_id, body.entry, body.changes,
                               body.set_id, baseline, body.label)
        except (AccessDenied, ManifestError, ExperimentRefused) as exc:
            raise refused(exc) from None

    @app.get("/experiments")
    def list_experiments(member_id: str | None = None, project_id: str | None = None) -> list[dict]:
        return lab.list(member_id, project_id)

    @app.post("/experiments/run")
    def run_experiments(body: RunExperimentsIn) -> list[dict]:
        """The researcher's Run / Run all. Experiments run one at a time, within their limits."""
        try:
            return lab.run(body.ids)
        except ExperimentNotFound as exc:
            raise refused(exc) from None

    @app.get("/experiments/{experiment_id}")
    def experiment(experiment_id: str) -> dict:
        """One experiment with its numbers, alerts and summary, and the same for its baseline log."""
        try:
            detail = lab.detail(experiment_id)
        except ExperimentNotFound as exc:
            raise refused(exc) from None
        detail["baseline_result"] = None
        if detail["baseline"]:
            try:
                g = registry.grant_for_root(detail["root"])
                _, text, _ = registry.read_file(detail["baseline"], g)
                detail["baseline_result"] = summarize(text.splitlines())
            except (AccessDenied, OSError):
                pass  # the folder is no longer granted or the log is gone
        return detail

    @app.post("/experiments/{experiment_id}/stop")
    def stop_experiment(experiment_id: str) -> dict:
        try:
            return lab.stop(experiment_id)
        except ExperimentNotFound as exc:
            raise refused(exc) from None

    @app.delete("/experiments/{experiment_id}")
    def delete_experiment(experiment_id: str) -> dict:
        try:
            lab.delete(experiment_id)
        except (ExperimentNotFound, ExperimentRefused) as exc:
            raise refused(exc) from None
        return {"status": "deleted"}

    return app


def _status(watch_id: str, w: dict) -> dict:
    return {"root": w["root"], "project_id": w["project_id"], "member_id": w["member_id"],
            **w["watcher"].status(), "file": w["file"], "undelivered": w["poster"].undelivered,
            "run_id": w["poster"].run_id}

