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

from backend.auth.permissions import PermissionDenied, require_project_member
from backend.auth.sessions import Sessions
from backend.creao import CreaoClient, personal_agent
from backend.db.database import Database
from backend.knowledge_sharing.publishing import (
    DraftAlreadyDecided,
    DraftNotFound,
    InvalidDraft,
    approve_draft,
    reject_draft,
)
from backend.memory.private_memory import PrivateMemory
from backend.memory.shared_findings import list_project_findings
from backend.orchestration.diagnosis import EvidenceRejected, prepare_diagnosis, record_diagnosis
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
    project_id: str = Field(min_length=1, max_length=64)
    symptom: str = Field(min_length=1, max_length=4000)
    focus_areas: list[str] = Field(default_factory=lambda: ["numerical"], max_length=6)
    analysis_depth: str = "standard"
    evidence: list[EvidenceIn] = Field(default_factory=list, max_length=MAX_ITEMS)


class SignInIn(BaseModel):
    member_id: str = Field(min_length=1, max_length=64)


class ApproveIn(BaseModel):
    """Optional edits made before approving; anything left out keeps the draft's value."""
    title: str | None = None
    summary: str | None = None
    category: str | None = None
    verification_status: str | None = None


def create_app(
    client_factory: Callable[[str], Any] | None = None,
    jobs: JobStore | None = None,
    data_dir: str | Path | None = None,
) -> FastAPI:
    """client_factory(agent_env_prefix) builds the CREAO client for a member's
    personal agent; tests pass a fake one. The databases are created (with the
    demo workspace) on first use."""
    app = FastAPI(title="SimGuard backend", version="0.1.0")
    factory = client_factory or (lambda prefix: CreaoClient(personal_agent(prefix)))
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

    def require_member_of(conn: Any, member_id: str, project_id: str) -> None:
        try:
            require_project_member(conn, member_id, project_id)
        except PermissionDenied:
            raise HTTPException(status_code=403, detail="You are not a member of that project.") from None

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
    def start_diagnosis(
        request: DiagnoseIn, background: BackgroundTasks, member: dict[str, Any] = Depends(current_member)
    ) -> dict[str, Any]:
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], request.project_id)
            project_name = conn.execute("SELECT name FROM projects WHERE id = ?", (request.project_id,)).fetchone()[0]
            agent = conn.execute(
                "SELECT creao_env_prefix FROM agents WHERE member_id = ? AND status = 'active'", (member["id"],)
            ).fetchone()
        if agent is None:
            raise HTTPException(status_code=403, detail="You have no active personal agent.")
        try:
            prepared = prepare_diagnosis(
                project_name, request.symptom, request.focus_areas, request.analysis_depth,
                [e.model_dump() for e in request.evidence],
            )
        except EvidenceRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

        job = store.create(member["id"], request.project_id)
        prefix = agent["creao_env_prefix"]
        background.add_task(
            run_job, store, job.job_id, lambda: factory(prefix), prepared,
            lambda diagnosis: record_diagnosis(db, member["id"], request.project_id, prepared.symptom, diagnosis),
        )
        return {
            "job_id": job.job_id,
            "status": job.status,
            "evidence_sent": [{"path": e["path"], "chars": len(e["excerpt"])} for e in prepared.evidence],
            "redactions": prepared.redactions,
        }

    @app.get("/diagnose/{job_id}")
    def get_diagnosis(job_id: str, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None or job["member_id"] != member["id"]:  # other members' jobs look like they don't exist
            raise HTTPException(status_code=404, detail="No diagnosis job with that ID.")
        return job

    @app.get("/drafts")
    def my_drafts(member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Your pending drafts. Only you can see them."""
        return PrivateMemory(db, member["id"]).drafts()

    @app.post("/drafts/{draft_id}/approve")
    def approve(draft_id: str, edits: ApproveIn, member: dict[str, Any] = Depends(current_member)) -> dict[str, Any]:
        try:
            return approve_draft(db, member["id"], draft_id, edits.model_dump())
        except DraftNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except DraftAlreadyDecided as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except InvalidDraft as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except PermissionDenied:
            raise HTTPException(status_code=403, detail="You are no longer a member of that project.") from None

    @app.post("/drafts/{draft_id}/reject")
    def reject(draft_id: str, member: dict[str, Any] = Depends(current_member)) -> dict[str, str]:
        try:
            reject_draft(db, member["id"], draft_id)
        except DraftNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except DraftAlreadyDecided as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except PermissionDenied:
            raise HTTPException(status_code=403, detail="You are no longer a member of that project.") from None
        return {"status": "rejected", "detail": "The draft stays private; nothing was shared."}

    @app.get("/projects/{project_id}/findings")
    def project_findings(project_id: str, member: dict[str, Any] = Depends(current_member)) -> list[dict[str, Any]]:
        """Approved findings in one project of the Super Brain."""
        with closing(db.shared()) as conn:
            require_member_of(conn, member["id"], project_id)
            return list_project_findings(conn, project_id)

    return app


app = create_app()
