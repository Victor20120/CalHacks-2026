"""Turning a private diagnosis into a shared Super Brain finding.

Sharing always needs the researcher's explicit approval. Until then the draft
stays in their private memory; a rejected draft never leaves it. Approving
writes the approval record and the finding together, in one transaction, so a
finding can't exist without its approval.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from backend.auth.permissions import require_project_member
from backend.db.database import Database
from backend.db.schema import FINDING_CATEGORIES, VERIFICATION_STATUSES
from backend.memory.private_memory import DRAFT_FIELDS, PrivateMemory
from backend.memory.shared_findings import get_finding
from backend.orchestration.evidence_policy import redact_secrets

MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 4_000


class DraftNotFound(Exception):
    pass


class DraftAlreadyDecided(Exception):
    pass


class InvalidDraft(Exception):
    pass


def draft_from_diagnosis(symptom: str, diagnosis: dict[str, Any]) -> dict[str, str] | None:
    """A minimum-necessary error-diagnosis draft from the agent's top issue, or None
    if the agent found nothing. Always starts as a hypothesis: the agent's
    "confirmed" is not a researcher's verification."""
    issues = diagnosis.get("issues") or []
    if not diagnosis.get("analysis_ran") or not issues:
        return None
    top = next((i for i in issues if i["status"] == "confirmed"), issues[0])
    summary = "\n".join([
        f"Symptom: {symptom}",
        f"Likely cause (agent says {top['status']}, {top['confidence'] or 'unknown'} confidence): {top['title']}",
        f"Evidence: {top['evidence']}",
        f"How to validate: {top['proposed_validation']}",
        "Not yet verified by a researcher.",
    ])
    return {
        "category": "error_diagnosis",
        "title": top["title"][:MAX_TITLE_CHARS],
        "summary": summary[:MAX_SUMMARY_CHARS],
        "verification_status": "hypothesis",
    }


def approve_draft(db: Database, member_id: str, draft_id: str, edits: dict[str, Any] | None = None) -> dict:
    """Publish one of the researcher's drafts, with their optional edits. Returns the finding."""
    memory = PrivateMemory(db, member_id)
    draft = _pending_draft(memory, draft_id)

    final = {k: draft[k] for k in DRAFT_FIELDS}
    changes = {k: v for k, v in (edits or {}).items() if k in DRAFT_FIELDS and v is not None}
    edited = any(final[k] != v for k, v in changes.items())
    final.update(changes)
    _validate(final)
    final["title"], _ = redact_secrets(final["title"].strip())
    final["summary"], _ = redact_secrets(final["summary"].strip())

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    approval_id, finding_id = str(uuid.uuid4()), str(uuid.uuid4())
    with closing(db.shared()) as conn:
        require_project_member(conn, member_id, draft["project_id"])
        workspace_id = conn.execute("SELECT workspace_id FROM projects WHERE id = ?", (draft["project_id"],)).fetchone()[0]
        try:
            with conn:
                conn.execute(
                    """INSERT INTO approvals (id, workspace_id, project_id, member_id, draft_id, edited, approved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (approval_id, workspace_id, draft["project_id"], member_id, draft_id, int(edited), now),
                )
                conn.execute(
                    """INSERT INTO shared_findings
                       (id, workspace_id, project_id, category, title, summary, verification_status,
                        contributed_by, approval_id, source_creao_run_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (finding_id, workspace_id, draft["project_id"], final["category"], final["title"], final["summary"],
                     final["verification_status"], member_id, approval_id, draft["source_creao_run_id"], now),
                )
        except sqlite3.IntegrityError:
            # Already published by an earlier approval that stopped before updating the private draft.
            row = conn.execute(
                "SELECT f.id FROM shared_findings f JOIN approvals a ON a.id = f.approval_id WHERE a.draft_id = ?",
                (draft_id,),
            ).fetchone()
            if row is None:
                raise
            finding_id = row[0]
        finding = get_finding(conn, finding_id)
    memory.decide_draft(draft_id, "approved", finding_id)
    return finding


def reject_draft(db: Database, member_id: str, draft_id: str) -> None:
    """Keep the draft private for good. Nothing is written to the shared database."""
    memory = PrivateMemory(db, member_id)
    _pending_draft(memory, draft_id)
    memory.decide_draft(draft_id, "rejected")


def _pending_draft(memory: PrivateMemory, draft_id: str) -> dict:
    draft = memory.get_draft(draft_id)
    if draft is None:
        raise DraftNotFound("No draft with that ID.")
    if draft["status"] != "pending":
        raise DraftAlreadyDecided(f"This draft was already {draft['status']}.")
    return draft


def _validate(final: dict[str, Any]) -> None:
    if final["category"] not in FINDING_CATEGORIES:
        raise InvalidDraft(f"category must be one of {', '.join(FINDING_CATEGORIES)}.")
    if final["verification_status"] not in VERIFICATION_STATUSES:
        raise InvalidDraft(f"verification_status must be one of {', '.join(VERIFICATION_STATUSES)}.")
    if not str(final["title"]).strip() or len(final["title"]) > MAX_TITLE_CHARS:
        raise InvalidDraft(f"title must be 1-{MAX_TITLE_CHARS} characters.")
    if not str(final["summary"]).strip() or len(final["summary"]) > MAX_SUMMARY_CHARS:
        raise InvalidDraft(f"summary must be 1-{MAX_SUMMARY_CHARS} characters.")
