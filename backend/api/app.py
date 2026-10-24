"""SimGuard backend API.

Run locally (binds to this computer only):
    python -m uvicorn backend.api.app:app --port 8000

No sign-in yet: workspace and project permissions come in build step 3.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.creao import CreaoClient, hub_agent
from backend.orchestration.diagnosis import EvidenceRejected, prepare_diagnosis
from backend.orchestration.evidence_policy import MAX_ITEMS
from backend.orchestration.jobs import JobStore, run_job


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


def create_app(client_factory: Callable[[], Any] | None = None, jobs: JobStore | None = None) -> FastAPI:
    """client_factory builds the CREAO client per job; tests pass a fake one."""
    app = FastAPI(title="SimGuard backend", version="0.1.0")
    factory = client_factory or (lambda: CreaoClient(hub_agent()))
    store = jobs or JobStore()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
