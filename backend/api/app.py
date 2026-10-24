"""SimGuard backend API.

Run locally (binds to this computer only):
    python -m uvicorn backend.api.app:app --port 8000

Sign-in is a prototype: pick a demo member (POST /sign-in) and send the
token as "Authorization: Bearer <token>".
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from backend.auth.sessions import Sessions
from backend.creao import CreaoClient, hub_agent
from backend.db.database import Database
from backend.orchestration.diagnosis import EvidenceRejected, prepare_diagnosis
from backend.orchestration.evidence_policy import MAX_ITEMS
from backend.orchestration.jobs import JobStore, run_job
from backend.workspaces.demo import seed_demo
from backend.workspaces.store import get_member, list_members, projects_for_member


class EvidenceIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="code", max_length=20)
    excerpt: str
    truncated: bool = False


class DiagnoseIn(BaseModel):
    project: str = Field(min_length=1, max_length=100)
    symptom: str = Field(min_length=1, max_length=4000)
    focus_areas: str | None = Field(default=None, max_length=500)
    evidence: list[EvidenceIn] = Field(default_factory=list, max_length=MAX_ITEMS)


class SignInIn(BaseModel):
    member_id: str = Field(min_length=1, max_length=64)


def create_app(
    client_factory: Callable[[], Any] | None = None,
    jobs: JobStore | None = None,
    data_dir: str | Path | None = None,
) -> FastAPI:
    """client_factory builds the CREAO client per job; tests pass a fake one.
    The databases are created (with the demo workspace) on first use."""
    app = FastAPI(title="SimGuard backend", version="0.1.0")
    factory = client_factory or (lambda: CreaoClient(hub_agent()))
    store = jobs or JobStore()
    db = Database(data_dir, seed=seed_demo)
    sessions = Sessions()

    def current_member(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """The signed-in member, re-read on every request so offboarding takes effect at once."""
        token = (authorization or "").removeprefix("Bearer ").strip()
        member_id = sessions.member_for(token) if token else None
        if member_id is None:
            raise HTTPException(status_code=401, detail="Sign in first: POST /sign-in")
        with closing(db.shared()) as conn:
            member = get_member(conn, member_id)
        if member is None or member["status"] != "active":
            raise HTTPException(status_code=403, detail="This member no longer has access.")
        return member

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/demo/members")
    def demo_members() -> list[dict[str, Any]]:
        """Who can sign in (prototype only)."""
        with closing(db.shared()) as conn:
            return [{k: m[k] for k in ("id", "display_name", "role")} for m in list_members(conn)]

    @app.post("/sign-in")
    def sign_in(request: SignInIn) -> dict[str, Any]:
        with closing(db.shared()) as conn:
            member = get_member(conn, request.member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="No member with that ID.")
        if member["status"] != "active":
            raise HTTPException(status_code=403, detail="This member no longer has access.")
        return {"token": sessions.sign_in(member["id"]), "member": member}

    @app.get("/me")
    def me(member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        with closing(db.shared()) as conn:
            projects = projects_for_member(conn, member["id"])
        return {"member": member, "projects": projects}

    @app.post("/diagnose", status_code=202)
    def start_diagnosis(request: DiagnoseIn, background: BackgroundTasks) -> dict[str, Any]:
        try:
            prepared = prepare_diagnosis(
                request.project, request.symptom, request.focus_areas, [e.model_dump() for e in request.evidence]
            )
        except EvidenceRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
        job = store.create()
        background.add_task(run_job, store, job.job_id, factory, prepared)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "evidence_sent": [{"path": e["path"], "chars": len(e["excerpt"])} for e in prepared.evidence],
            "redactions": prepared.redactions,
        }

    @app.get("/diagnose/{job_id}")
    def get_diagnosis(job_id: str) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No diagnosis job with that ID.")
        return job

    return app


app = create_app()
