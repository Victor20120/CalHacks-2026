"""The local connector as a small service for the dashboard, on this computer only.

A browser can't read local files, so the dashboard asks this service, which runs
on the researcher's computer, to list granted folders and their files, extract
evidence, and watch run logs. It enforces the same folder grants and evidence
rules as the command line, and it never starts, pauses or stops a simulation.

    python -m local_connector serve        (listens on 127.0.0.1:8001)
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any, Callable

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.orchestration.evidence_policy import MAX_ITEMS
from local_connector.evidence.extract import extract_evidence, file_kind
from local_connector.evidence.project_map import build_project_map
from local_connector.file_access.grants import AccessDenied, Grant, GrantRegistry
from local_connector.file_access.walk import iter_project_files
from local_connector.simulation_monitor.checks import Alert
from local_connector.simulation_monitor.watcher import LogWatcher

log = logging.getLogger(__name__)

DEFAULT_BACKEND = "http://127.0.0.1:8000"
MAX_LISTED_FILES = 500


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


class BackendPoster:
    """Sends a watcher's alerts to the backend as the researcher (prototype sign-in).
    If the backend can't be reached, the alerts stay visible in the watch status."""

    def __init__(self, backend_url: str, member_id: str, project_id: str, run_label: str, http: Any):
        self.backend_url, self.member_id, self.project_id, self.run_label = backend_url, member_id, project_id, run_label
        self.http = http
        self.undelivered = 0
        self._token: str | None = None

    def __call__(self, alerts: list[Alert]) -> None:
        body = {"project_id": self.project_id, "run_label": self.run_label, "alerts": [a.to_dict() for a in alerts]}
        try:
            if self._token is None:
                signed_in = self.http.post(f"{self.backend_url}/sign-in", json={"member_id": self.member_id}, timeout=10)
                signed_in.raise_for_status()
                self._token = signed_in.json()["token"]
            sent = self.http.post(f"{self.backend_url}/alerts", json=body, timeout=10,
                                  headers={"Authorization": f"Bearer {self._token}"})
            if sent.status_code == 401:  # the backend restarted; sign in again next time
                self._token = None
            sent.raise_for_status()
        except Exception:
            self.undelivered += len(alerts)
            log.warning("Could not send %d alert(s) to the backend", len(alerts))


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
        return {"status": "stopped", "detail": "Stopped watching. The simulation was not touched."}

    return app


def _status(watch_id: str, w: dict) -> dict:
    return {"root": w["root"], "project_id": w["project_id"], "member_id": w["member_id"],
            **w["watcher"].status(), "file": w["file"], "undelivered": w["poster"].undelivered}

