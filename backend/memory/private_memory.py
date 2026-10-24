"""One researcher's private memory, split by project.

It lives in that researcher's own database file, never in the shared Super
Brain. Every read and write first checks that the researcher is still an
active member of the project.
"""
from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import datetime, timezone

from backend.auth.permissions import require_project_member
from backend.db.database import Database
from backend.workspaces.store import projects_for_member

EPISODE_KINDS = ("incident", "debugging_attempt", "test", "diagnosis", "note")
EPISODE_STATUSES = ("open", "hypothesis", "confirmed", "resolved", "unresolved")
DRAFT_FIELDS = ("category", "title", "summary", "verification_status")
CHAT_SETTINGS = {"project": "agent_chat", "all": "agent_chat_all", "personal": "personal_agent_chat"}


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

    def get_episode(self, episode_id: str) -> dict | None:
        with closing(self.db.private(self.member_id)) as conn:
            row = conn.execute("SELECT * FROM private_episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            return None
        self._check(row["project_id"])
        return dict(row)

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

    # --- Project context: the saved overview of each project ---

    def save_project_context(self, project_id: str, overview: dict | None, reply: str, map_stats: dict,
                             creao_run_id: str | None) -> None:
        self._check(project_id)
        with closing(self.db.private(self.member_id)) as conn:
            conn.execute(
                """INSERT INTO project_context (project_id, overview, reply, map_stats, creao_run_id, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET overview = excluded.overview, reply = excluded.reply,
                       map_stats = excluded.map_stats, creao_run_id = excluded.creao_run_id,
                       updated_at = excluded.updated_at""",
                (project_id, json.dumps(overview or {}), reply, json.dumps(map_stats), creao_run_id, _now()),
            )
            conn.commit()

    def project_context(self, project_id: str) -> dict | None:
        self._check(project_id)
        with closing(self.db.private(self.member_id)) as conn:
            row = conn.execute("SELECT * FROM project_context WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        context = dict(row)
        context["overview"] = json.loads(context["overview"]) or None
        context["map_stats"] = json.loads(context["map_stats"])
        return context

    # --- Settings and alert history (history stays off unless the researcher turns it on) ---

    def agent_rules(self, project_id: str) -> str:
        from backend.orchestration.chat import DEFAULT_AGENT_RULES
        return self._project_setting(project_id, "agent_rules", DEFAULT_AGENT_RULES)

    def set_agent_rules(self, project_id: str, rules: str) -> None:
        self._set_project_setting(project_id, "agent_rules", rules)

    def chat_history(self, project_id: str, scope: str = "project") -> list[dict]:
        name = CHAT_SETTINGS[scope]
        return json.loads(self._project_setting(project_id, name, "[]"))

    def save_chat_exchange(self, project_id: str, question: str, result: dict, scope: str = "project") -> dict:
        from backend.orchestration.chat import MAX_CHAT_EXCHANGES
        self._check(project_id)
        with closing(self.db.private(self.member_id)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            name = CHAT_SETTINGS[scope]
            key = f"{name}:{project_id}"
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            history = json.loads(row["value"]) if row else []
            exchange = {"id": str(uuid.uuid4()), "question": question, **result, "created_at": _now()}
            history.append(exchange)
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (key, json.dumps(history[-MAX_CHAT_EXCHANGES:])))
            conn.commit()
        return exchange

    def clear_chat(self, project_id: str, scope: str = "project") -> None:
        self._set_project_setting(project_id, CHAT_SETTINGS[scope], "[]")

    def _project_setting(self, project_id: str, name: str, default: str) -> str:
        self._check(project_id)
        with closing(self.db.private(self.member_id)) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (f"{name}:{project_id}",)).fetchone()
        return row["value"] if row else default

    def _set_project_setting(self, project_id: str, name: str, value: str) -> None:
        self._check(project_id)
        with closing(self.db.private(self.member_id)) as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (f"{name}:{project_id}", value))
            conn.commit()

    def alert_history_enabled(self) -> bool:
        with closing(self.db.private(self.member_id)) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'save_alert_history'").fetchone()
        return row is not None and row["value"] == "on"

    def set_alert_history(self, enabled: bool) -> None:
        with closing(self.db.private(self.member_id)) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('save_alert_history', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("on" if enabled else "off",),
            )
            conn.commit()

    def save_alerts(self, project_id: str, alerts: list[dict]) -> int:
        """Save alerts to history. Callers only do this while history is turned on."""
        self._check(project_id)
        with closing(self.db.private(self.member_id)) as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO alerts
                   (id, project_id, run_label, severity, check_name, message, metric, line_number, line, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(a["id"], project_id, a["run_label"], a["severity"], a["check"], a["message"], a["metric"],
                  a["line_number"], a["line"], a["created_at"]) for a in alerts],
            )
            conn.commit()
        return len(alerts)

    def saved_alerts(self) -> list[dict]:
        """Saved alerts, newest first, only for projects the researcher is still in."""
        with closing(self.db.shared()) as conn:
            project_ids = [p["id"] for p in projects_for_member(conn, self.member_id)]
        if not project_ids:
            return []
        marks = ",".join("?" * len(project_ids))
        with closing(self.db.private(self.member_id)) as conn:
            rows = conn.execute(
                f"""SELECT id, project_id, run_label, severity, check_name AS "check", message, metric,
                           line_number, line, created_at
                    FROM alerts WHERE project_id IN ({marks}) ORDER BY created_at DESC, rowid DESC""",
                project_ids,
            )
            return [dict(row) for row in rows]

    def delete_saved_alerts(self) -> int:
        with closing(self.db.private(self.member_id)) as conn:
            deleted = conn.execute("DELETE FROM alerts").rowcount
            conn.commit()
        return deleted

    def _check(self, project_id: str) -> None:
        with closing(self.db.shared()) as conn:
            require_project_member(conn, self.member_id, project_id)
