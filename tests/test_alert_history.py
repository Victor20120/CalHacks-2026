"""Tests for opt-in alert history: off by default, saved privately only while on."""
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.db.database import Database

ALERT = {"severity": "critical", "check": "nan_inf", "message": "HISTORY-MARKER max|T| became inf",
         "metric": "max|T|", "line_number": 24, "line": "step 2223 max|T| = inf"}


class Lab:
    def __init__(self, data):
        self.data = data
        self.client = TestClient(create_app(client_factory=lambda prefix: None, data_dir=data))
        self.db = Database(data)
        self._headers = {}

    def headers(self, member="researcher_a"):
        if member not in self._headers:
            token = self.client.post("/sign-in", json={"member_id": member}).json()["token"]
            self._headers[member] = {"Authorization": f"Bearer {token}"}
        return self._headers[member]

    def post_alert(self, message=ALERT["message"], member="researcher_a"):
        body = {"project_id": "heat-diffusion", "run_label": "run.log", "alerts": [{**ALERT, "message": message}]}
        return self.client.post("/alerts", json=body, headers=self.headers(member)).json()

    def set_history(self, on, member="researcher_a"):
        return self.client.put("/settings", json={"save_alert_history": on}, headers=self.headers(member))

    def history(self, member="researcher_a"):
        return self.client.get("/alerts/history", headers=self.headers(member)).json()

    def saved_rows(self, member="researcher_a"):
        with closing(self.db.private(member)) as conn:
            return conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path / "data")


def test_history_is_off_by_default_and_nothing_is_saved(lab):
    assert lab.client.get("/settings", headers=lab.headers()).json() == {"save_alert_history": False}

    response = lab.post_alert()

    assert response == {"stored": 1, "saved": False}
    assert lab.saved_rows() == 0
    for db_file in lab.data.rglob("*.db"):
        assert b"HISTORY-MARKER" not in db_file.read_bytes()


def test_turning_history_on_saves_alerts_to_private_memory(lab):
    lab.set_history(True)

    assert lab.post_alert()["saved"] is True
    assert [a["message"] for a in lab.history()] == [ALERT["message"]]
    assert lab.history()[0]["check"] == "nan_inf"
    assert b"HISTORY-MARKER" not in lab.db.shared_path.read_bytes()  # private file only


def test_saved_history_survives_a_backend_restart(lab):
    lab.set_history(True)
    lab.post_alert()

    restarted = Lab(lab.data)

    assert restarted.client.get("/alerts", headers=restarted.headers()).json() == []  # session alerts are gone
    assert [a["message"] for a in restarted.history()] == [ALERT["message"]]


def test_turning_history_off_stops_saving(lab):
    lab.set_history(True)
    lab.post_alert("first")
    lab.set_history(False)

    lab.post_alert("second")

    assert [a["message"] for a in lab.history()] == ["first"]


def test_saved_history_can_be_deleted(lab):
    lab.set_history(True)
    lab.post_alert()

    deleted = lab.client.delete("/alerts/history", headers=lab.headers()).json()

    assert deleted == {"deleted": 1}
    assert lab.history() == []


def test_history_is_private_to_each_researcher(lab):
    lab.set_history(True)
    lab.post_alert()
    lab.set_history(True, member="researcher_b")

    assert lab.history(member="researcher_b") == []
    assert lab.client.get("/alerts/history").status_code == 401
