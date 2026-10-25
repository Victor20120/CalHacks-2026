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

from pathlib import Path

import logging
import hashlib
import os
import threading
import time
import uuid
from typing import Any, Callable

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from backend.orchestration.evidence_policy import MAX_ITEMS, redact_secrets
from local_connector.agent_tools import list_project_files, read_project_file
from local_connector.references import PaperUnreadable, list_papers, project_references, read_code, read_paper
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
from local_connector.simulations import SimulationManager, SimulationNotFound
from local_connector.reports import FrozenReports
from local_connector.context import atomic_save, export_records, acknowledge_events, read_artifact
from backend.orchestration.simulation_setup import InvalidSetup

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


class FolderIn(BaseModel):
    folder: str = Field(min_length=1, max_length=255)  # a granted folder's name, never a local path


class ListIn(FolderIn):
    under: str = Field(default="", max_length=300)  # a subfolder, relative to the granted folder


class ReadIn(FolderIn):
    path: str = Field(min_length=1, max_length=500)
    lines: str | None = Field(default=None, max_length=20)


class ReferencesIn(FolderIn):
    path: str = Field(min_length=1, max_length=500)  # the file a lesson is about


class PaperIn(FolderIn):
    path: str = Field(min_length=1, max_length=500)  # the paper: PDF, .tex, .md or .txt
    pages: str | None = Field(default=None, max_length=20)  # a PDF's pages, "3-7"


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
    origin_run_ref: str | None = Field(default=None, max_length=100)
    origin_anomaly_ref: str | None = Field(default=None, max_length=100)
    origin_investigation_ref: str | None = Field(default=None, max_length=100)


class ContextAckIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    event_ids: list[str] = Field(max_length=200)


class ContextArtifactIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    record_ref: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=30)
    relative_path: str = Field(min_length=1, max_length=500)


class RunExperimentsIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=20)


class SimulationIn(RootIn):
    project_id: str = Field(min_length=1, max_length=64)
    environment: str | None = Field(default=None, max_length=1000)
    compiler: str | None = Field(default=None, max_length=1000)
    origin_run_ref: str | None = Field(default=None, max_length=100)
    origin_anomaly_ref: str | None = Field(default=None, max_length=100)
    origin_investigation_ref: str | None = Field(default=None, max_length=100)


class SimulationEnvironmentIn(BaseModel):
    environment: str | None = Field(default=None, max_length=1000)
    compiler: str | None = Field(default=None, max_length=1000)


class SimulationPlanIn(BaseModel):
    plan: dict[str, Any]


class SimulationPlanningIn(BaseModel):
    job_id: str | None = Field(default=None, min_length=1, max_length=64)
    error: str = Field(default="", max_length=1000)


class BackendPoster:
    """Deliver frozen run batches, including old runs waiting through an outage.

    Each run keeps its own identity and bounded observation buffers. An optional
    local state file preserves ACK retries and queued runs after a process restart;
    it contains observation metadata, never credentials or complete log files.
    """

    def __init__(self, backend_url: str, member_id: str, project_id: str, run_label: str, http: Any,
                 state_file: str | Path | None = None):
        self.backend_url, self.member_id, self.project_id, self.run_label = backend_url, member_id, project_id, run_label
        self.http = http
        self.undelivered = 0
        self._token: str | None = None
        self._state_file = Path(state_file) if state_file else None
        self._current = self._empty_run()
        self._queued: list[dict] = []
        self._restore()
        if self._current["finished"]:
            self._current = self._empty_run()

    @staticmethod
    def _empty_run() -> dict:
        return {"run_id": None, "reports": FrozenReports(), "points": [], "alerts": [],
                "stopping": False, "finished": False}

    @property
    def run_id(self) -> str | None:
        return self._current["run_id"]

    @property
    def _reports(self) -> FrozenReports:
        return self._current["reports"]

    @property
    def _points(self) -> list[dict]:
        return self._current["points"]

    @property
    def _alerts(self) -> list[dict]:
        return self._current["alerts"]

    def __call__(self, alerts: list[Alert]) -> None:
        body = {"project_id": self.project_id, "run_label": self.run_label, "alerts": [a.to_dict() for a in alerts]}
        try:
            self._post("/alerts", body)
        except Exception:
            self.undelivered += len(alerts)
            log.warning("Could not send %d alert(s) to the backend", len(alerts))
        self._alerts.extend(a.to_dict() for a in alerts)
        self._bound_buffers()
        self._report()

    def start(self) -> None:
        """Put the run on the dashboard before the log has any numbers."""
        self._report()

    def samples(self, samples: list[Sample]) -> None:
        self._points.extend(s.to_dict() for s in samples)
        self._bound_buffers()
        self._report()

    def stopped(self) -> None:
        self._report(stopped=True)

    def new_run(self) -> None:
        """Queue the old run's final delivery without blocking a new simulation."""
        self._report(stopped=True)
        if not self._current["finished"]:
            self._queued.append(self._current)
        self._current = self._empty_run()
        self._save()

    def _bound_buffers(self) -> None:
        # A pending prefix must stay byte-for-byte frozen. Keep recent unsent
        # observations after it when an outage outlasts the buffer capacity.
        pending = self._reports.pending or {}
        for key, limit in (("points", MAX_PENDING_POINTS), ("alerts", MAX_REPORT_ALERTS * 10)):
            values = self._current[key]
            excess = len(values) - limit
            if excess > 0:
                prefix = len(pending.get(key, []))
                del values[prefix:prefix + excess]
                if key == "alerts":
                    self.undelivered += excess
                log.warning("Run delivery buffer dropped %d unsent %s during an outage", excess, key)

    def _report(self, stopped: bool = False) -> None:
        self._current["stopping"] = self._current["stopping"] or stopped
        self._save()
        try:
            while self._queued:
                self._deliver(self._queued[0])
                self._queued.pop(0)
                self._save()
            self._deliver(self._current)
        except Exception:
            log.warning("Could not send the run's numbers to the backend; will try again")

    def _deliver(self, state: dict) -> None:
        if state["finished"]:
            return
        reports = state["reports"]
        if state["run_id"] is None:
            run = self._post("/simulation-runs", {"project_id": self.project_id, "run_label": self.run_label,
                                                 "client_run_id": reports.client_run_id})
            state["run_id"] = run["id"]
            self._save()
        while True:
            points, alerts = state["points"][:MAX_REPORT_POINTS], state["alerts"][:MAX_REPORT_ALERTS]
            last = len(points) == len(state["points"]) and len(alerts) == len(state["alerts"])
            body = reports.freeze(points, alerts, state["stopping"] and last)
            self._save()  # Persist the exact key and payload before they may be accepted remotely.
            self._post(f"/simulation-runs/{state['run_id']}/report", body)
            point_count, alert_count = reports.acknowledge(body["batch_id"])
            del state["points"][:point_count], state["alerts"][:alert_count]
            if not state["points"] and not state["alerts"] and (not state["stopping"] or body["stopped"]):
                state["finished"] = body["stopped"]
                self._save()
                return
            self._save()

    def _save(self) -> None:
        if self._state_file is None:
            return
        import json

        def encoded(state: dict) -> dict:
            return {**state, "reports": state["reports"].state()}

        payload = {"version": 1, "identity": [self.backend_url, self.member_id, self.project_id, self.run_label],
                   "current": encoded(self._current), "queued": [encoded(state) for state in self._queued]}
        try:
            atomic_save(self._state_file, payload)
        except (OSError, ValueError):
            log.warning("Could not save local run delivery state")

    def _restore(self) -> None:
        if self._state_file is None or not self._state_file.exists():
            return
        import json

        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            identity = [self.backend_url, self.member_id, self.project_id, self.run_label]
            if payload.get("version") != 1 or payload.get("identity") != identity:
                return

            def decoded(state: dict) -> dict:
                return {**state, "reports": FrozenReports.from_state(state["reports"])}

            current = decoded(payload["current"])
            queued = [decoded(state) for state in payload["queued"]]
            self._current, self._queued = current, queued
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("Could not restore local run delivery state")

    def _post(self, path: str, body: dict) -> Any:
        if self._token is None:
            signed_in = self.http.post(f"{self.backend_url}/sign-in", json={"member_id": self.member_id}, timeout=10)
            signed_in.raise_for_status()
            self._token = signed_in.json()["token"]
        sent = self.http.post(f"{self.backend_url}{path}", json=body, timeout=10,
                              headers={"Authorization": f"Bearer {self._token}"})
        if sent.status_code == 401:
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
    simulations: SimulationManager | None = None,
) -> FastAPI:
    app = FastAPI(title="ExpandRG local connector", version="0.1.0")
    registry = registry or GrantRegistry()
    backend = (backend_url or os.environ.get("EXPANDRG_BACKEND") or DEFAULT_BACKEND).rstrip("/")
    http = http or requests.Session()
    simulation_store = simulations or SimulationManager()
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

    def named(folder: str) -> Grant:
        try:
            return registry.grant_named(folder)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @app.get("/visualize/local-data")
    def graph_local_data(run_id: str, run_label: str, member_id: str, project_id: str, full: bool = False) -> dict:
        """Read-only log/manifest data. Backend checks run visibility before calling.

        File reads still require a live grant, including after a grant is revoked.
        No file path supplied by a graph or agent is ever executed.
        """
        from local_connector.visualization import local_data
        try:
            record = next((r for r in simulation_store.records.values()
                           if r.get("backend_run_id") == run_id and r["member_id"] == member_id
                           and r["project_id"] == project_id), None)
            if record:
                source = Path(record.get("runtime", {}).get("source_root", ""))
                return local_data(registry, run_label, full, path=simulation_store.home / record["id"] / "output.log",
                                  source_root=source)
            return local_data(registry, run_label, full)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/tools/list_project_files")
    def tool_list_files(body: ListIn) -> dict:
        """The research files a personal agent may ask for, in a granted folder (read-only)."""
        try:
            return list_project_files(named(body.folder), body.under)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @app.post("/tools/read_project_file")
    def tool_read_file(body: ReadIn) -> dict:
        """One research file, or a range of its lines, for a personal agent (read-only)."""
        try:
            return read_project_file(registry, named(body.folder), body.path, body.lines)
        except (AccessDenied, ValueError) as exc:
            raise HTTPException(status_code=403 if isinstance(exc, AccessDenied) else 422, detail=str(exc)) from None

    @app.post("/tools/project_references")
    def tool_references(body: ReferencesIn) -> dict:
        """The papers the project cites (DOIs, arXiv IDs, BibTeX entries), for a lesson (read-only)."""
        try:
            return project_references(registry, named(body.folder), body.path)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @app.post("/tools/read_code")
    def tool_read_code(body: ReferencesIn) -> dict:
        """The whole code file a lesson is about, numbered, up to about 1,200 lines (read-only)."""
        try:
            return read_code(registry, named(body.folder), body.path)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @app.post("/tools/list_papers")
    def tool_list_papers(body: FolderIn) -> dict:
        """The files in a granted folder that can be a lesson's paper (read-only)."""
        return list_papers(named(body.folder))

    @app.post("/tools/read_paper")
    def tool_read_paper(body: PaperIn) -> dict:
        """A lesson's paper as text: a PDF page by page (needs pypdf), or a .tex, .md or .txt file (read-only)."""
        try:
            return read_paper(registry, named(body.folder), body.path, body.pages)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except PaperUnreadable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

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
        identity = "\0".join((backend, body.member_id, body.project_id, str(g.root), relative))
        delivery_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        poster = BackendPoster(backend, body.member_id, body.project_id, relative, http,
                               state_file=registry.state_file.parent / "run-deliveries" / (delivery_key + ".json"))
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

    # --- Simulations: detect setup, save a version, explicitly run, auto-monitor ---

    def simulation_member(authorization: str | None, project_id: str) -> tuple[dict, str]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Sign in to ExpandRG first.")
        try:
            response = http.get(backend + "/me", headers={"Authorization": authorization}, timeout=10)
        except requests.RequestException:
            raise HTTPException(status_code=503, detail="The ExpandRG backend is not available.") from None
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Sign in again; access could not be checked.")
        me = response.json()
        if me["member"]["role"] != "researcher" or project_id not in {p["id"] for p in me["projects"]}:
            raise HTTPException(status_code=403, detail="Choose one of your research projects.")
        return me["member"], authorization.removeprefix("Bearer ")

    def owned_simulation(identifier: str, authorization: str | None) -> tuple[dict, dict, str]:
        if not authorization:
            raise HTTPException(status_code=401, detail="Sign in first.")
        try:
            record = simulation_store.get(identifier)
        except SimulationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        member, token = simulation_member(authorization, record["project_id"])
        if member["id"] != record["member_id"]:
            raise HTTPException(status_code=404, detail="No saved simulation with that ID.")
        return record, member, token

    @app.post("/simulations/inspect", status_code=201)
    def inspect_simulation(body: SimulationIn, authorization: str | None = Header(default=None)) -> dict:
        member, _ = simulation_member(authorization, body.project_id)
        grant = grant_for(body.root)
        try:
            record = simulation_store.prepare(grant, member["id"], body.project_id, body.environment, body.compiler,
                                              origin_run_ref=body.origin_run_ref,
                                              origin_anomaly_ref=body.origin_anomaly_ref,
                                              origin_investigation_ref=body.origin_investigation_ref)
            return {"simulation": record, "context": simulation_store.context(record["id"], registry)}
        except (AccessDenied, ExperimentRefused, InvalidSetup) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/simulations")
    def simulations_list(project_id: str, authorization: str | None = Header(default=None)) -> list[dict]:
        member, _ = simulation_member(authorization, project_id)
        return simulation_store.list(member["id"], project_id)

    @app.get("/simulations/{identifier}")
    def simulation_detail(identifier: str, authorization: str | None = Header(default=None)) -> dict:
        owned_simulation(identifier, authorization)
        return simulation_store.detail(identifier)

    @app.get("/simulations/{identifier}/context")
    def simulation_context(identifier: str, authorization: str | None = Header(default=None)) -> dict:
        owned_simulation(identifier, authorization)
        return simulation_store.context(identifier, registry)

    @app.post("/simulations/{identifier}/planning")
    def simulation_planning(identifier: str, body: SimulationPlanningIn,
                            authorization: str | None = Header(default=None)) -> dict:
        owned_simulation(identifier, authorization)
        try:
            if body.error:
                return simulation_store.setup_failed(identifier, body.error)
            if not body.job_id:
                raise InvalidSetup("A setup job ID is required.")
            return simulation_store.planning(identifier, body.job_id)
        except InvalidSetup as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/simulations/{identifier}/plan")
    def simulation_plan(identifier: str, body: SimulationPlanIn,
                        authorization: str | None = Header(default=None)) -> dict:
        owned_simulation(identifier, authorization)
        try:
            return simulation_store.set_plan(identifier, body.plan)
        except InvalidSetup as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/simulations/{identifier}/environment")
    def simulation_environment(identifier: str, body: SimulationEnvironmentIn,
                               authorization: str | None = Header(default=None)) -> dict:
        owned_simulation(identifier, authorization)
        try:
            return simulation_store.connect_environment(identifier, body.environment, body.compiler)
        except InvalidSetup as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/simulations/{identifier}/run")
    def run_simulation(identifier: str, authorization: str | None = Header(default=None)) -> dict:
        _, member, token = owned_simulation(identifier, authorization)
        def monitor(record):
            title = record["plan"]["title"]
            poster = BackendPoster(backend, member["id"], record["project_id"], f"Version {record['version']} · {title}", http,
                                   state_file=simulation_store.home / record["id"] / "delivery.json")
            poster._token = token
            poster.start()
            if not poster.run_id:
                raise InvalidSetup("Monitoring could not connect. Try again when the backend is available.")
            return StreamWatch(poster), poster.run_id
        try:
            return simulation_store.run(identifier, monitor)
        except InvalidSetup as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/simulations/{identifier}/stop")
    def stop_simulation(identifier: str, authorization: str | None = Header(default=None)) -> dict:
        owned_simulation(identifier, authorization)
        return simulation_store.stop(identifier)

    @app.delete("/simulations/{identifier}")
    def delete_simulation(identifier: str, authorization: str | None = Header(default=None)) -> dict:
        """Delete one of your saved versions from this computer (not while it runs). Its results in Monitor stay."""
        owned_simulation(identifier, authorization)
        try:
            simulation_store.delete(identifier)
        except InvalidSetup as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"status": "deleted"}

    @app.post("/simulations/{identifier}/repeat", status_code=201)
    def repeat_simulation(identifier: str, authorization: str | None = Header(default=None)) -> dict:
        owned_simulation(identifier, authorization)
        return simulation_store.repeat(identifier)

    # --- Experiments: prepared in a copy, run only on the researcher's click ---

    lab = experiments or ExperimentManager()

    @app.get("/context/records")
    def context_records(project_id: str, record_ref: str | None = None,
                        authorization: str | None = Header(default=None)) -> dict:
        member, _ = simulation_member(authorization, project_id)
        return export_records(simulation_store, lab, member["id"], project_id, record_ref=record_ref)

    @app.post("/context/events/ack")
    def context_ack(body: ContextAckIn, authorization: str | None = Header(default=None)) -> dict:
        member, _ = simulation_member(authorization, body.project_id)
        return acknowledge_events(simulation_store, lab, member["id"], body.project_id, body.event_ids)

    @app.post("/context/artifacts/read")
    def context_artifact(body: ContextArtifactIn, authorization: str | None = Header(default=None)) -> dict:
        member, _ = simulation_member(authorization, body.project_id)
        try:
            return read_artifact(simulation_store, lab, member["id"], body.project_id, body.record_ref,
                                 body.role, body.relative_path, registry)
        except AccessDenied as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

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
                               body.set_id, baseline, body.label, origin_run_ref=body.origin_run_ref,
                               origin_anomaly_ref=body.origin_anomaly_ref,
                               origin_investigation_ref=body.origin_investigation_ref)
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
        from local_connector.visualization import manifest_units
        try:
            detail["units"] = manifest_units(registry, Path(detail["root"]))
        except AccessDenied:
            detail["units"] = {}
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

