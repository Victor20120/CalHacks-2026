"""Turning a private diagnosis into a shared Super Brain finding.

A diagnosis is shared only with the researcher's explicit approval. Until then
the draft stays in their private memory; a rejected draft never leaves it.
Approving writes the approval record, the finding and its links together, in
one transaction, so a finding can't exist without its approval.

What a personal agent establishes in chat is shared by the agent itself (the
group's choice): always as a hypothesis, marked "shared by the agent from chat",
with the agent's own guess kept apart. Members can then add detail, their own
guess or a correction to any finding they can read.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from backend.auth.permissions import require_project_member
from backend.db.database import Database
from backend.db.schema import FINDING_CATEGORIES, VERIFICATION_STATUSES
from backend.memory.private_memory import DRAFT_FIELDS, PrivateMemory
from backend.memory.shared_findings import finding_comments, finding_notes, get_finding, get_readable_finding
from backend.orchestration.evidence_policy import redact_secrets
from backend.workspaces.store import is_project_member

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


def approve_draft(db: Database, member_id: str, draft_id: str, edits: dict[str, Any] | None = None,
                  run_id: str | None = None) -> dict:
    """Publish one of the researcher's drafts, with their optional edits and the run it came
    from (one of their own runs in the draft's project). Returns the finding."""
    memory = PrivateMemory(db, member_id)
    draft = _pending_draft(memory, draft_id)

    final = {k: draft[k] for k in DRAFT_FIELDS}
    changes = {k: v for k, v in (edits or {}).items() if k in DRAFT_FIELDS and v is not None}
    edited = any(final[k] != v for k, v in changes.items())
    final.update(changes)
    _validate(final)
    final["title"], _ = redact_secrets(final["title"].strip())
    final["summary"], _ = redact_secrets(final["summary"].strip())
    used = _context_finding_ids(memory, draft)
    # The canonical investigation keeps the simulation identity independently
    # of its CREAO execution ID. Reuse that explicit source on approval.
    if run_id is None and draft.get("episode_id"):
        episode = memory.get_episode(draft["episode_id"])
        try:
            source = json.loads(episode["details"]).get("simulation_run_ref") if episode else None
        except (ValueError, AttributeError):
            source = None
        if isinstance(source, str) and source.startswith("run:"):
            with closing(db.shared()) as conn:
                candidate = source.removeprefix("run:")
                if _is_own_run(conn, member_id, draft["project_id"], candidate):
                    run_id = candidate

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    approval_id, finding_id = str(uuid.uuid4()), str(uuid.uuid4())
    with closing(db.shared()) as conn:
        require_project_member(conn, member_id, draft["project_id"])
        _check_own_run(conn, member_id, draft["project_id"], run_id)
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
                if run_id:
                    conn.execute("INSERT INTO finding_runs (finding_id, run_id, attached_at) VALUES (?, ?, ?)",
                                 (finding_id, run_id, now))
                # Link to the earlier findings this diagnosis used. "associated_with", not
                # "similar_to": using a finding as context doesn't show the problems are the same.
                for prior_id in used:
                    prior = conn.execute("SELECT project_id FROM shared_findings WHERE id = ?", (prior_id,)).fetchone()
                    if prior and is_project_member(conn, member_id, prior["project_id"]):
                        conn.execute(
                            """INSERT OR IGNORE INTO finding_links (from_finding_id, to_finding_id, relation, created_at)
                               VALUES (?, ?, 'associated_with', ?)""",
                            (finding_id, prior_id, now),
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


MAX_GUESS_CHARS = 1_000
MAX_NOTE_CHARS = 2_000
NOTE_KINDS = ("detail", "guess", "correction")


def share_from_chat(db: Database, member_id: str, project_id: str, share: Any, run_id: str | None,
                    simulation_run_id: str | None = None) -> dict | None:
    """Share what the member's personal agent established in a chat, or None if the
    agent's share block is empty or unusable. Always a hypothesis: an agent can't
    verify anything. The same title from the same member in the project isn't shared twice.
    simulation_run_id: the member's run the agent looked at, attached if it's in this project."""
    if not isinstance(share, dict):
        return None
    title = str(share.get("title") or "").strip()
    summary = str(share.get("summary") or "").strip()
    if not title or not summary:
        return None
    evidence = str(share.get("evidence") or "").strip()
    if evidence:
        summary = f"{summary}\nEvidence: {evidence}"
    summary += "\nShared by the researcher's personal agent from a chat; not yet reviewed by the researcher."
    category = share.get("category") if share.get("category") in FINDING_CATEGORIES else "research_result"
    title, _ = redact_secrets(title[:MAX_TITLE_CHARS])
    summary, _ = redact_secrets(summary[:MAX_SUMMARY_CHARS])
    guess, _ = redact_secrets(str(share.get("agent_guess") or "").strip()[:MAX_GUESS_CHARS])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    approval_id, finding_id = str(uuid.uuid4()), str(uuid.uuid4())
    with closing(db.shared()) as conn:
        require_project_member(conn, member_id, project_id)
        repeat = conn.execute(
            """SELECT f.id FROM shared_findings f JOIN auto_shares s ON s.finding_id = f.id
               WHERE f.project_id = ? AND f.contributed_by = ? AND lower(f.title) = lower(?)""",
            (project_id, member_id, title),
        ).fetchone()
        if repeat:
            return None
        workspace_id = conn.execute("SELECT workspace_id FROM projects WHERE id = ?", (project_id,)).fetchone()[0]
        with conn:
            conn.execute(
                """INSERT INTO approvals (id, workspace_id, project_id, member_id, draft_id, edited, approved_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (approval_id, workspace_id, project_id, member_id, f"chat:{finding_id}", now),
            )
            conn.execute(
                """INSERT INTO shared_findings
                   (id, workspace_id, project_id, category, title, summary, verification_status,
                    contributed_by, approval_id, source_creao_run_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'hypothesis', ?, ?, ?, ?)""",
                (finding_id, workspace_id, project_id, category, title, summary, member_id, approval_id, run_id, now),
            )
            conn.execute("INSERT INTO auto_shares (finding_id, source, agent_guess, created_at) VALUES (?, 'chat', ?, ?)",
                         (finding_id, guess, now))
            if simulation_run_id and _is_own_run(conn, member_id, project_id, simulation_run_id):
                conn.execute("INSERT INTO finding_runs (finding_id, run_id, attached_at) VALUES (?, ?, ?)",
                             (finding_id, simulation_run_id, now))
        return get_finding(conn, finding_id)


def add_finding_note(db: Database, member_id: str, finding_id: str, kind: str, text: str) -> dict:
    """Add detail, the member's own guess, or a correction to a finding they can read."""
    if kind not in NOTE_KINDS:
        raise InvalidDraft(f"kind must be one of {', '.join(NOTE_KINDS)}.")
    text, _ = redact_secrets(text.strip()[:MAX_NOTE_CHARS])
    if not text:
        raise InvalidDraft("Write something first.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.shared()) as conn:
        if get_readable_finding(conn, member_id, finding_id) is None:
            raise DraftNotFound("No finding with that ID.")
        with conn:
            conn.execute(
                "INSERT INTO finding_notes (id, finding_id, member_id, kind, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), finding_id, member_id, kind, text, now),
            )
        return finding_notes(conn, finding_id)[-1]


def _is_own_run(conn: sqlite3.Connection, member_id: str, project_id: str, run_id: str) -> bool:
    row = conn.execute("SELECT member_id, project_id FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
    return row is not None and row["member_id"] == member_id and row["project_id"] == project_id


def _check_own_run(conn: sqlite3.Connection, member_id: str, project_id: str, run_id: str | None) -> None:
    if run_id and not _is_own_run(conn, member_id, project_id, run_id):
        raise InvalidDraft("Attach one of your own runs in this project.")


def add_finding_comment(db: Database, member_id: str, finding_id: str, text: str) -> dict:
    """Feedback on a finding the member can read: the leader and the project's researchers
    write and see the same thread."""
    text, _ = redact_secrets(text.strip()[:MAX_NOTE_CHARS])
    if not text:
        raise InvalidDraft("Write something first.")
    with closing(db.shared()) as conn:
        if get_readable_finding(conn, member_id, finding_id) is None:
            raise DraftNotFound("No finding with that ID.")
        with conn:
            conn.execute("INSERT INTO finding_comments (id, finding_id, member_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
                         (str(uuid.uuid4()), finding_id, member_id, text,
                          datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return finding_comments(conn, finding_id)[-1]


def reject_draft(db: Database, member_id: str, draft_id: str) -> None:
    """Keep the draft private for good. Nothing is written to the shared database."""
    memory = PrivateMemory(db, member_id)
    _pending_draft(memory, draft_id)
    memory.decide_draft(draft_id, "rejected")


def _context_finding_ids(memory: PrivateMemory, draft: dict) -> list[str]:
    """The Super Brain findings the draft's diagnosis was given as context."""
    episode = memory.get_episode(draft["episode_id"]) if draft["episode_id"] else None
    if episode is None:
        return []
    try:
        return list(json.loads(episode["details"]).get("context_finding_ids", []))
    except (ValueError, AttributeError):
        return []


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
