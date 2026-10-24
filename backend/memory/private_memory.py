"""One researcher's private memory, split by project.

It lives in that researcher's own database file, never in the shared Super
Brain. Every read and write first checks that the researcher is still an
active member of the project.
"""
from __future__ import annotations

import uuid
from contextlib import closing
from datetime import datetime, timezone

from backend.auth.permissions import require_project_member
from backend.db.database import Database
from backend.workspaces.store import projects_for_member

EPISODE_KINDS = ("incident", "debugging_attempt", "test", "diagnosis", "note")
EPISODE_STATUSES = ("open", "hypothesis", "confirmed", "resolved", "unresolved")
DRAFT_FIELDS = ("category", "title", "summary", "verification_status")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PrivateMemory:
    def __init__(self, db: Database, member_id: str):
        db.private_path(member_id)  # validates the member ID
        self.db = db
        self.member_id = member_id

    def add_episode(
        self,
        project_id: str,
        kind: str,
        summary: str,
        details: str = "",
        status: str = "open",
        creao_run_id: str | None = None,
    ) -> str:
        if kind not in EPISODE_KINDS:
            raise ValueError(f"Episode kind must be one of {EPISODE_KINDS}.")
        if status not in EPISODE_STATUSES:
            raise ValueError(f"Episode status must be one of {EPISODE_STATUSES}.")
        self._check(project_id)
        episode_id = str(uuid.uuid4())
        with closing(self.db.private(self.member_id)) as conn:
            conn.execute(
                """INSERT INTO private_episodes
                   (id, project_id, kind, summary, details, status, creao_run_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (episode_id, project_id, kind, summary, details, status, creao_run_id, _now()),
            )
            conn.commit()
        return episode_id

    def episodes(self, project_id: str) -> list[dict]:
        self._check(project_id)
        with closing(self.db.private(self.member_id)) as conn:
            rows = conn.execute(
                "SELECT * FROM private_episodes WHERE project_id = ? ORDER BY created_at, id", (project_id,)
            )
            return [dict(row) for row in rows]

    # --- Drafts proposed for the Super Brain (private until approved) ---

    def add_draft(
        self, project_id: str, draft: dict, source_creao_run_id: str | None = None, episode_id: str | None = None
    ) -> str:
        self._check(project_id)
        draft_id = str(uuid.uuid4())
        with closing(self.db.private(self.member_id)) as conn:
            conn.execute(
                """INSERT INTO share_drafts
                   (id, project_id, category, title, summary, verification_status,
                    source_creao_run_id, episode_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (draft_id, project_id, *(draft[k] for k in DRAFT_FIELDS), source_creao_run_id, episode_id, _now()),
            )
            conn.commit()
        return draft_id

    def get_draft(self, draft_id: str) -> dict | None:
        """One of this researcher's drafts, or None. Refused if they left its project."""
        with closing(self.db.private(self.member_id)) as conn:
            row = conn.execute("SELECT * FROM share_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return None
        self._check(row["project_id"])
        return dict(row)

    def drafts(self, status: str = "pending") -> list[dict]:
        """This researcher's drafts, only for projects they are still a member of."""
        with closing(self.db.shared()) as conn:
            project_ids = [p["id"] for p in projects_for_member(conn, self.member_id)]
        if not project_ids:
            return []
        marks = ",".join("?" * len(project_ids))
        with closing(self.db.private(self.member_id)) as conn:
            rows = conn.execute(
                f"SELECT * FROM share_drafts WHERE status = ? AND project_id IN ({marks}) ORDER BY created_at, id",
                (status, *project_ids),
            )
            return [dict(row) for row in rows]

    def decide_draft(self, draft_id: str, status: str, finding_id: str | None = None) -> None:
        """Mark a pending draft approved or rejected."""
        if status not in ("approved", "rejected"):
            raise ValueError("A draft can only be approved or rejected.")
        with closing(self.db.private(self.member_id)) as conn:
            conn.execute(
                "UPDATE share_drafts SET status = ?, finding_id = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                (status, finding_id, _now(), draft_id),
            )
            conn.commit()

    def _check(self, project_id: str) -> None:
        with closing(self.db.shared()) as conn:
            require_project_member(conn, self.member_id, project_id)
