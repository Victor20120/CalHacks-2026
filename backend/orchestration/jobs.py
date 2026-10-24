"""Diagnosis jobs. A CREAO run takes about a minute, so the API starts a job
and the caller polls it. Prototype: jobs live in memory and are lost when the
backend restarts (durable storage comes with SQLite in build step 3)."""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from backend.creao import CreaoConfigError, CreaoInputError, CreaoRequestError, CreaoRunFailed, CreaoTimeout
from backend.orchestration.diagnosis import PreparedDiagnosis, run_diagnosis

log = logging.getLogger(__name__)


@dataclass
class Job:
    job_id: str
    status: str  # "running", "completed" or "failed"
    created_at: str
    diagnosis: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(job_id=str(uuid.uuid4()), status="running",
                  created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return asdict(job) if job else None

    def finish(self, job_id: str, diagnosis: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job_id].status = "completed"
            self._jobs[job_id].diagnosis = diagnosis

    def fail(self, job_id: str, error: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job_id].status = "failed"
            self._jobs[job_id].error = error


def run_job(store: JobStore, job_id: str, client_factory: Callable[[], Any], prepared: PreparedDiagnosis) -> None:
    """Run one diagnosis and record the outcome. Never logs evidence or replies."""
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
        store.finish(job_id, diagnosis)
        log.info("Diagnosis job %s completed (CREAO run %s)", job_id, diagnosis["run_id"])
