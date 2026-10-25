"""Frozen simulation reports: retries send the same key and the same contents.

This helper performs no I/O. A connector can save ``state()`` beside its local
watcher state, then restore it after restart with ``from_state()``. Credentials
are deliberately absent, so signing in again never changes ingestion identity.
"""
from __future__ import annotations

from copy import deepcopy
import uuid


class FrozenReports:
    def __init__(self, client_run_id: str | None = None, producer_id: str | None = None):
        self.client_run_id = client_run_id or str(uuid.uuid4())
        self.producer_id = producer_id or str(uuid.uuid4())
        self.source_revision = 0
        self._pending: dict | None = None

    def freeze(self, points: list[dict], alerts: list[dict], stopped: bool = False) -> dict:
        """Freeze the buffered prefix, or return an unchanged pending retry.

        New observations may accumulate in the caller's buffers while a batch
        is pending. They become the next batch only after this one is ACKed.
        Returned copies cannot change the frozen payload accidentally.
        """
        if self._pending is None:
            self.source_revision += 1
            self._pending = {
                "batch_id": str(uuid.uuid4()), "producer_id": self.producer_id,
                "source_revision": self.source_revision, "points": deepcopy(points),
                "alerts": deepcopy(alerts), "stopped": bool(stopped),
            }
        return deepcopy(self._pending)

    def acknowledge(self, batch_id: str) -> tuple[int, int] | None:
        """Clear only the ACKed batch and return buffered prefix lengths to remove."""
        if self._pending is None or self._pending["batch_id"] != batch_id:
            return None
        counts = len(self._pending["points"]), len(self._pending["alerts"])
        self._pending = None
        return counts

    @property
    def pending(self) -> dict | None:
        """A copy for inspecting the frozen prefix without changing retry data."""
        return deepcopy(self._pending)

    def new_run(self) -> None:
        """A real new simulation gets a new run identity and revision sequence."""
        self.client_run_id = str(uuid.uuid4())
        self.source_revision = 0
        self._pending = None

    def state(self) -> dict:
        """JSON-compatible retry state; the caller controls where it is persisted."""
        return deepcopy({"client_run_id": self.client_run_id, "producer_id": self.producer_id,
                         "source_revision": self.source_revision, "pending": self._pending})

    @classmethod
    def from_state(cls, state: dict) -> FrozenReports:
        result = cls(state["client_run_id"], state["producer_id"])
        result.source_revision = state["source_revision"]
        result._pending = deepcopy(state["pending"])
        return result
