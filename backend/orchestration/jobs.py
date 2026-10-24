"""Diagnosis jobs. A CREAO run takes about a minute, so the API starts a job
and the caller polls it. Prototype: jobs live in memory and are lost when the
backend restarts; the diagnosis itself is saved to the researcher's private
memory when it finishes."""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from backend.creao import CreaoConfigError, CreaoInputError, CreaoRequestError, CreaoRunFailed, CreaoTimeout
from backend.orchestration.diagnosis import PreparedDiagnosis, run_diagnosis

log = logging.getLogger(__name__)


@dataclass
class Job:
    job_id: str
    member_id: str
    project_id: str
    status: str  # "running", "completed" or "failed"
    created_at: str
    prior_findings: list[dict[str, Any]] = field(default_factory=list)  # Super Brain findings given as context
    diagnosis: dict[str, Any] | None = None
    draft_id: str | None = None  # the private draft proposed for sharing, if any
    error: dict[str, Any] | None = None
    warning: str | None = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, member_id: str, project_id: str, prior_findings: list[dict[str, Any]] | None = None) -> Job:
        job = Job(job_id=str(uuid.uuid4()), member_id=member_id, project_id=project_id, status="running",
                  created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  prior_findings=list(prior_findings or []))
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return asdict(job) if job else None

    def finish(self, job_id: str, diagnosis: dict[str, Any], draft_id: str | None, warning: str | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status, job.diagnosis, job.draft_id, job.warning = "completed", diagnosis, draft_id, warning

    def fail(self, job_id: str, error: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job_id].status = "failed"
            self._jobs[job_id].error = error


def run_job(
    store: JobStore,
    job_id: str,
    client_factory: Callable[[], Any],
    prepared: PreparedDiagnosis,
    on_success: Callable[[dict[str, Any]], str | None] | None = None,
) -> None:
    """Run one diagnosis and record the outcome. on_success saves it privately and
    returns a draft ID. Never logs evidence or replies."""
    try:
        diagnosis = run_diagnosis(client_factory(), prepared)
    except CreaoTimeout as exc:
        store.fail(job_id, {"type": "timeout", "message": str(exc), "run_id": exc.run_id})
    except CreaoRunFailed as exc:
        store.fail(job_id, {"type": "run_failed", "message": str(exc), "run_id": exc.run_id})
    except CreaoInputError as exc:
        store.fail(job_id, {"type": "input", "message": str(exc)})
    except CreaoConfigError as exc:
        store.fail(job_id, {"type": "config", "message": str(exc)})
    except CreaoRequestError as exc:
        store.fail(job_id, {"type": "creao_request", "message": str(exc)})
    except Exception:
        log.exception("Diagnosis job %s failed unexpectedly", job_id)
        store.fail(job_id, {"type": "internal", "message": "Unexpected error; see the backend log."})
    else:
        draft_id, warning = None, None
        if on_success:
            try:
                draft_id = on_success(diagnosis)
            except Exception:
                log.exception("Diagnosis job %s: could not save to private memory", job_id)
                warning = "The diagnosis could not be saved to private memory, so no draft was made."
        store.finish(job_id, diagnosis, draft_id, warning)
        log.info("Diagnosis job %s completed (CREAO run %s)", job_id, diagnosis["run_id"])
