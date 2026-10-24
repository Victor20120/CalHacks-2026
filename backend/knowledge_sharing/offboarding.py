"""Offboarding: hand a leaving researcher's eligible project work to the Super
Brain, verify it, then retire their personal agent.

This is the group's membership policy (OFFBOARDING_POLICY), disclosed in
advance, and the one exception to per-finding approval. The steps, in order:
1. the group leader starts it;
2. the member's access is revoked at once;
3. eligible project work is prepared from their private memory: diagnoses,
   test results, debugging outcomes, unresolved tasks and pending drafts;
4. left out: personal notes, drafts they chose to keep private, the agent's
   conversation text, anything already shared and anything outside this
   group's projects; secrets are blanked and each record keeps its project,
   so only that project's members can read it;
5. the records are transferred in one transaction and verified before it commits;
6. the member's personal agent is marked inactive.
If transfer or verification fails, nothing is stored, the handoff is recorded
as failed and the agent is not retired, so the leader can try again. Private
memory is not deleted here; it follows a separate retention policy.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.db.database import Database
from backend.knowledge_sharing.publishing import MAX_SUMMARY_CHARS, MAX_TITLE_CHARS
from backend.orchestration.evidence_policy import redact_secrets
from backend.workspaces.store import get_member

OFFBOARDING_POLICY = (
    "Membership policy: when you leave this research group, your eligible project work (diagnoses, test "
    "results, debugging outcomes, unresolved tasks and drafts you had not decided on) is handed to the group's "
    "Super Brain, visible only to members of the project it belongs to. Personal notes, drafts you chose to keep "
    "private, conversations with your agent and anything outside this group are never handed off. Your personal "
    "agent is then retired."
)

EPISODE_CATEGORY = {"diagnosis": "error_diagnosis", "debugging_attempt": "error_diagnosis",
                    "incident": "error_diagnosis", "test": "research_result"}
EPISODE_VERIFICATION = {"open": "unresolved", "hypothesis": "hypothesis", "confirmed": "supported",
                        "resolved": "supported", "unresolved": "unresolved"}
MAX_DETAILS_CHARS = 1_500


class OffboardingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class HandoffRecord:
    source: str  # "episode" or "draft"
    project_id: str
    category: str
    title: str
    summary: str
    verification_status: str
    source_creao_run_id: str | None


def offboard_member(db: Database, leader_id: str, member_id: str) -> dict:
    """Run the whole handoff -> verify -> retire workflow and return a report."""
    now = _now()
    with closing(db.shared()) as conn:
        leader = get_member(conn, leader_id)
        member = get_member(conn, member_id)
        if leader is None or leader["role"] != "leader" or leader["status"] != "active":
            raise OffboardingError("Only the group leader can offboard a member.", 403)
        if member is None or member["workspace_id"] != leader["workspace_id"]:
            raise OffboardingError("No member with that ID in your group.", 404)
        if member["role"] == "leader":
            raise OffboardingError("The group leader can't be offboarded here.", 400)
        if conn.execute("SELECT 1 FROM handoff_events WHERE member_id = ? AND status = 'completed'",
                        (member_id,)).fetchone():
            raise OffboardingError("This member has already been offboarded.", 409)

        # Steps 1-2: start, and revoke access at once (offboarded members fail every access check).
        handoff_id = str(uuid.uuid4())
        with conn:
            conn.execute(
                """INSERT INTO handoff_events (id, workspace_id, member_id, initiated_by, status, started_at)
                   VALUES (?, ?, ?, ?, 'started', ?)""",
                (handoff_id, leader["workspace_id"], member_id, leader_id, now),
            )
            conn.execute("UPDATE members SET status = 'offboarded' WHERE id = ?", (member_id,))
        project_ids = [r[0] for r in conn.execute(
            "SELECT pm.project_id FROM project_members pm JOIN projects p ON p.id = pm.project_id "
            "WHERE pm.member_id = ? AND p.workspace_id = ?", (member_id, leader["workspace_id"]))]

    # Steps 3-4: prepare eligible work, leaving out everything that isn't.
    records, excluded = prepare_handoff(db, member_id, set(project_ids), member["display_name"])

    # Step 5: transfer and verify in one transaction.
    finding_ids: list[str] = []
    error = None
    with closing(db.shared()) as conn:
        try:
            with conn:
                for n, record in enumerate(records):
                    approval_id, finding_id = str(uuid.uuid4()), str(uuid.uuid4())
                    conn.execute(
                        """INSERT INTO approvals (id, workspace_id, project_id, member_id, draft_id, edited, approved_at)
                           VALUES (?, ?, ?, ?, ?, 0, ?)""",
                        (approval_id, leader["workspace_id"], record.project_id, leader_id,
                         f"handoff:{handoff_id}:{n}", now),
                    )
                    conn.execute(
                        """INSERT INTO shared_findings
                           (id, workspace_id, project_id, category, title, summary, verification_status,
                            contributed_by, approval_id, source_creao_run_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (finding_id, leader["workspace_id"], record.project_id, record.category, record.title,
                         record.summary, record.verification_status, member_id, approval_id,
                         record.source_creao_run_id, now),
                    )
                    conn.execute("INSERT INTO handoff_records (handoff_id, finding_id, source) VALUES (?, ?, ?)",
                                 (handoff_id, finding_id, record.source))
                    finding_ids.append(finding_id)
                _verify(conn, handoff_id, records)
        except Exception as exc:  # nothing was committed
            error = f"Handoff failed and nothing was stored: {exc}"
            finding_ids = []

        # Step 6: retire the agent, only after a verified transfer.
        with conn:
            if error is None:
                conn.execute("UPDATE agents SET status = 'inactive' WHERE member_id = ?", (member_id,))
            conn.execute(
                """UPDATE handoff_events SET status = ?, records_prepared = ?, records_transferred = ?,
                          error = ?, finished_at = ? WHERE id = ?""",
                ("failed" if error else "completed", len(records), len(finding_ids), error, _now(), handoff_id),
            )

    done = error is None
    return {
        "handoff_id": handoff_id,
        "member_id": member_id,
        "member_name": member["display_name"],
        "status": "completed" if done else "failed",
        "records_prepared": len(records),
        "records_transferred": len(finding_ids),
        "finding_ids": finding_ids,
        "excluded": dict(excluded),
        "error": error,
        "steps": [
            {"step": "Offboarding started by the group leader", "done": True},
            {"step": "Access revoked", "done": True},
            {"step": f"Prepared {len(records)} eligible record(s)", "done": True},
            {"step": f"Left out {sum(excluded.values())} item(s): " + (
                ", ".join(f"{n} {why}" for why, n in excluded.items()) or "nothing"), "done": True},
            {"step": f"Transferred and verified {len(finding_ids)} record(s)", "done": done},
            {"step": "Personal agent retired", "done": done},
        ],
        "note": "Private memory was not deleted; it follows the group's separate retention policy.",
    }


def prepare_handoff(
    db: Database, member_id: str, project_ids: set[str], member_name: str
) -> tuple[list[HandoffRecord], Counter]:
    """Eligible records from the member's private memory, and counts of what was left out and why."""
    with closing(db.private(member_id)) as conn:
        drafts = [dict(r) for r in conn.execute("SELECT * FROM share_drafts ORDER BY created_at, id")]
        episodes = [dict(r) for r in conn.execute("SELECT * FROM private_episodes ORDER BY created_at, id")]

    provenance = (f"Handed off when {member_name} left the group (offboarding policy); "
                  f"not individually approved by {member_name}.")
    excluded: Counter = Counter()
    records: list[HandoffRecord] = []
    covered_episodes = {d["episode_id"] for d in drafts if d["episode_id"]}

    for d in drafts:
        if d["project_id"] not in project_ids:
            excluded["outside this group's projects"] += 1
        elif d["status"] == "approved":
            excluded["already shared"] += 1
        elif d["status"] == "rejected":
            excluded["drafts they chose to keep private"] += 1
        else:
            records.append(_record("draft", d["project_id"], d["category"], d["title"],
                                   f"{d['summary']}\n\n{provenance}", d["verification_status"], d["source_creao_run_id"]))

    for e in episodes:
        if e["project_id"] not in project_ids:
            excluded["outside this group's projects"] += 1
        elif e["kind"] == "note":
            excluded["personal notes"] += 1
        elif e["id"] in covered_episodes:
            continue  # its draft decided it (handed off if pending, left out if shared or rejected)
        else:
            records.append(_record("episode", e["project_id"], EPISODE_CATEGORY.get(e["kind"], "error_diagnosis"),
                                   e["summary"], _episode_summary(e, provenance),
                                   EPISODE_VERIFICATION.get(e["status"], "hypothesis"), e["creao_run_id"]))
    return records, excluded


def _episode_summary(episode: dict, provenance: str) -> str:
    """What happened, without the agent's conversation text."""
    parts = [f"Outcome: {episode['summary']} (status: {episode['status']})"]
    if episode["kind"] == "diagnosis":
        try:
            details = json.loads(episode["details"] or "{}")
        except ValueError:
            details = {}
        if details.get("symptom"):
            parts.insert(0, f"Symptom: {details['symptom']}")
        issues = details.get("issues") or []
        if issues:
            parts.append("Issues the agent listed:\n" + "\n".join(
                f"- [{i.get('status', 'suspected')}] {i.get('title', '')} — how to check: {i.get('proposed_validation', '')}"
                for i in issues))
    elif episode["details"]:
        parts.append(f"Details: {episode['details'][:MAX_DETAILS_CHARS]}")
    parts.append(provenance)
    return "\n\n".join(parts)


def _record(source, project_id, category, title, summary, verification, run_id) -> HandoffRecord:
    title, _ = redact_secrets(title.strip()[:MAX_TITLE_CHARS])
    summary, _ = redact_secrets(summary.strip())
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 6].rstrip() + " [...]"
    # Only the author can verify a finding; a handoff never carries "verified" over.
    verification = "supported" if verification == "verified" else verification
    return HandoffRecord(source, project_id, category, title or "Untitled work record", summary, verification, run_id)


def _verify(conn, handoff_id: str, records: list[HandoffRecord]) -> None:
    """Check every record was stored as prepared, before the transaction commits."""
    stored = conn.execute(
        """SELECT f.title, f.project_id FROM handoff_records hr JOIN shared_findings f ON f.id = hr.finding_id
           WHERE hr.handoff_id = ? ORDER BY f.rowid""",
        (handoff_id,),
    ).fetchall()
    if [(r["title"], r["project_id"]) for r in stored] != [(r.title, r.project_id) for r in records]:
        raise RuntimeError("the stored records don't match the prepared ones")


def handoff_history(conn, workspace_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT h.*, m.display_name AS member_name FROM handoff_events h JOIN members m ON m.id = h.member_id
           WHERE h.workspace_id = ? ORDER BY h.started_at DESC""",
        (workspace_id,),
    )
    return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
