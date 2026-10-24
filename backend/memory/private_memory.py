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

EPISODE_KINDS = ("incident", "debugging_attempt", "test", "diagnosis", "note")
EPISODE_STATUSES = ("open", "hypothesis", "confirmed", "resolved", "unresolved")


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
                (episode_id, project_id, kind, summary, details, status, creao_run_id,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
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

    def _check(self, project_id: str) -> None:
        with closing(self.db.shared()) as conn:
            require_project_member(conn, self.member_id, project_id)
