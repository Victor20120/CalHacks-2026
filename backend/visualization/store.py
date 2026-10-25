"""Saved graphs, in the researcher's private database: the spec, its fingerprints, the graph's
conversation with their agent, and every change (who made it and the spec after it), so any
step can be undone. Nobody else can read them.
"""
from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import datetime, timezone

from backend.db.database import Database

MAX_SAVED_EXCHANGES = 12


class SavedGraphNotFound(LookupError):
    pass


class SavedGraphConflict(RuntimeError):
    """The graph changed while an agent or another writer was preparing an edit."""


class SavedGraphs:
    def __init__(self, db: Database, member_id: str):
        db.private_path(member_id)  # validates the member ID
        self.db, self.member_id = db, member_id

    def list(self, project_id: str) -> list[dict]:
        with closing(self.db.private(self.member_id)) as conn:
            rows = conn.execute("SELECT id, project_id, title, created_at, updated_at FROM visualizations"
                                " WHERE project_id = ? ORDER BY updated_at DESC, rowid DESC", (project_id,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, graph_id: str) -> dict:
        with closing(self.db.private(self.member_id)) as conn:
            row = conn.execute("SELECT * FROM visualizations WHERE id = ?", (graph_id,)).fetchone()
            if row is None:
                raise SavedGraphNotFound("No saved graph with that ID.")
            history = conn.execute("SELECT at, actor, ops, creao_run_id FROM visualization_history"
                                   " WHERE visualization_id = ? ORDER BY rowid", (graph_id,)).fetchall()
        graph = dict(row)
        for key in ("spec", "fingerprints", "conversation"):
            graph[key] = json.loads(graph[key])
        graph["history"] = [{**dict(h), "ops": json.loads(h["ops"])} for h in history]
        return graph

    def create(self, project_id: str, title: str, spec: dict, fingerprints: dict) -> dict:
        graph_id, now = str(uuid.uuid4()), _now()
        with closing(self.db.private(self.member_id)) as conn, conn:
            conn.execute("INSERT INTO visualizations (id, project_id, title, spec, fingerprints, created_at, updated_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (graph_id, project_id, title, json.dumps(spec), json.dumps(fingerprints), now, now))
            self._history(conn, graph_id, "researcher", [{"op": "save_visualization", "title": title}], spec, None)
        return self.get(graph_id)

    def update(self, graph_id: str, spec: dict, fingerprints: dict, ops: list, actor: str = "researcher",
               creao_run_id: str | None = None, title: str | None = None, expected_spec: dict | None = None) -> dict:
        with closing(self.db.private(self.member_id)) as conn, conn:
            row = conn.execute("SELECT title, spec FROM visualizations WHERE id = ?", (graph_id,)).fetchone()
            if row is None:
                raise SavedGraphNotFound("No saved graph with that ID.")
            if expected_spec is not None and json.loads(row["spec"]) != expected_spec:
                raise SavedGraphConflict("This graph changed while the edit was being prepared. The newer graph was kept.")
            updated = conn.execute("UPDATE visualizations SET spec = ?, fingerprints = ?, title = ?, updated_at = ?"
                                   " WHERE id = ? AND spec = ?",
                                   (json.dumps(spec), json.dumps(fingerprints), title or row["title"], _now(), graph_id, row["spec"]))
            if updated.rowcount != 1:
                raise SavedGraphConflict("This graph changed while the edit was being prepared. The newer graph was kept.")
            if ops:
                self._history(conn, graph_id, actor, ops, spec, creao_run_id)
        return self.get(graph_id)

    def undo(self, graph_id: str) -> dict:
        """Back to the spec before the last change. The undo is itself recorded."""
        self.get(graph_id)
        with closing(self.db.private(self.member_id)) as conn, conn:
            rows = conn.execute("SELECT spec, ops FROM visualization_history WHERE visualization_id = ? ORDER BY rowid",
                                (graph_id,)).fetchall()
            states = []
            for row in rows:
                if json.loads(row["ops"]) == [{"op": "undo"}]:
                    if len(states) > 1:
                        states.pop()
                else:
                    states.append(row["spec"])
            if len(states) < 2:
                raise SavedGraphNotFound("There's nothing to undo.")
            previous = states[-2]
            conn.execute("UPDATE visualizations SET spec = ?, updated_at = ? WHERE id = ?", (previous, _now(), graph_id))
            self._history(conn, graph_id, "researcher", [{"op": "undo"}], json.loads(previous), None)
        return self.get(graph_id)

    def add_exchange(self, graph_id: str, exchange: dict) -> None:
        with closing(self.db.private(self.member_id)) as conn, conn:
            row = conn.execute("SELECT conversation FROM visualizations WHERE id = ?", (graph_id,)).fetchone()
            if row is None:
                return
            conversation = (json.loads(row["conversation"]) + [exchange])[-MAX_SAVED_EXCHANGES:]
            conn.execute("UPDATE visualizations SET conversation = ? WHERE id = ?", (json.dumps(conversation), graph_id))

    def delete(self, graph_id: str) -> None:
        self.get(graph_id)
        with closing(self.db.private(self.member_id)) as conn, conn:
            conn.execute("DELETE FROM visualization_history WHERE visualization_id = ?", (graph_id,))
            conn.execute("DELETE FROM visualizations WHERE id = ?", (graph_id,))

    @staticmethod
    def _history(conn, graph_id: str, actor: str, ops: list, spec: dict, creao_run_id: str | None) -> None:
        conn.execute("INSERT INTO visualization_history (id, visualization_id, at, actor, ops, spec, creao_run_id)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (str(uuid.uuid4()), graph_id, _now(), actor, json.dumps(ops), json.dumps(spec), creao_run_id))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
