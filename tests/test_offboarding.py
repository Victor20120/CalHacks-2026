"""Tests for offboarding: handoff -> verify -> retire, with Researcher C's demo history."""
import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.db.database import Database
from backend.knowledge_sharing import offboarding
from backend.memory.private_memory import PrivateMemory
from backend.workspaces.demo import C_DRAFTS, C_EPISODES, seed_demo_private


class Lab:
    def __init__(self, tmp_path):
        data = tmp_path / "data"
        self.client = TestClient(create_app(client_factory=lambda prefix: None, data_dir=data))
        self.db = Database(data)
        self._headers = {}
        self.client.get("/demo/members")  # creates the shared database
        seed_demo_private(self.db)

    def headers(self, member):
        if member not in self._headers:
            token = self.client.post("/sign-in", json={"member_id": member}).json()["token"]
            self._headers[member] = {"Authorization": f"Bearer {token}"}
        return self._headers[member]

    def offboard(self, member_id="researcher_c", by="leader"):
        return self.client.post("/offboarding", json={"member_id": member_id}, headers=self.headers(by))

    def scalar(self, sql, *params):
        with closing(self.db.shared()) as conn:
            return conn.execute(sql, params).fetchone()[0]


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


def test_only_the_leader_can_offboard(lab):
    assert lab.offboard(by="researcher_a").status_code == 403
    assert lab.client.post("/offboarding", json={"member_id": "researcher_c"}).status_code == 401
    assert lab.offboard(member_id="leader").status_code == 400
    assert lab.offboard(member_id="nobody").status_code == 404


def test_handoff_transfers_eligible_work_and_leaves_out_the_rest(lab):
    report = lab.offboard().json()

    assert report["status"] == "completed"
    assert report["records_prepared"] == report["records_transferred"] == 3
    assert report["excluded"] == {"personal notes": 1, "drafts they chose to keep private": 1}
    assert all(step["done"] for step in report["steps"])
    assert "not deleted" in report["note"]


def test_handed_off_findings_keep_their_project_and_are_marked(lab):
    lab.offboard()

    findings = lab.client.get("/projects/heat-diffusion/findings", headers=lab.headers("researcher_b")).json()
    titles = {f["title"]: f for f in findings}
    assert set(titles) == {
        "Crank-Nicolson time stepping stays stable at dt = 0.05 on the 41x41 grid",
        "Boundary rows stay frozen at their initial values",
        "Startup check refuses unstable time steps",
    }
    for f in findings:
        assert f["handed_off"] is True
        assert f["contributed_by"] == "researcher_c"
        assert f["verification_status"] != "verified"
        assert "not individually approved by Researcher C" in f["summary"]
    assert titles["Boundary rows stay frozen at their initial values"]["verification_status"] == "unresolved"
    assert titles["Startup check refuses unstable time steps"]["category"] == "code_change"


def test_excluded_items_never_reach_the_shared_database(lab):
    lab.offboard()

    shared = lab.db.shared_path.read_bytes()
    note = next(e for e in C_EPISODES if e[1] == "note")
    rejected = next(d for d in C_DRAFTS if d[1] == "rejected")
    assert note[3].encode() not in shared and note[4].encode() not in shared
    assert rejected[3].encode() not in shared and rejected[4].encode() not in shared


def test_access_is_revoked_and_the_agent_retired(lab):
    c_headers = lab.headers("researcher_c")  # signed in before offboarding

    lab.offboard()

    assert lab.client.get("/me", headers=c_headers).status_code == 403
    assert lab.client.post("/sign-in", json={"member_id": "researcher_c"}).status_code == 403
    assert lab.scalar("SELECT status FROM agents WHERE member_id = 'researcher_c'") == "inactive"
    assert lab.scalar("SELECT status FROM members WHERE id = 'researcher_c'") == "offboarded"


def test_private_memory_is_not_deleted(lab):
    lab.offboard()

    assert lab.db.private_path("researcher_c").exists()
    with closing(lab.db.private("researcher_c")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM private_episodes").fetchone()[0] == len(C_EPISODES)


def test_conversations_secrets_and_other_groups_are_left_out(lab):
    marker = "AGENT-CONVERSATION-MARKER"
    with closing(lab.db.private("researcher_c")) as conn:
        details = json.dumps({"symptom": "blew up at step 900", "reply": marker,
                              "issues": [{"title": "dt too large", "status": "confirmed", "proposed_validation": "rerun"}]})
        conn.execute("""INSERT INTO private_episodes (id, project_id, kind, summary, details, status, created_at)
                        VALUES ('c-diag', 'heat-diffusion', 'diagnosis', 'dt too large', ?, 'hypothesis', 'now')""",
                     (details,))
        conn.execute("""INSERT INTO private_episodes (id, project_id, kind, summary, details, status, created_at)
                        VALUES ('c-secret', 'heat-diffusion', 'test', 'Cluster run', 'password = hunter2', 'resolved', 'now')""")
        conn.execute("""INSERT INTO private_episodes (id, project_id, kind, summary, details, status, created_at)
                        VALUES ('c-other', 'another-groups-project', 'test', 'OTHER-GROUP-MARKER', '', 'resolved', 'now')""")
        conn.commit()

    report = lab.offboard().json()

    shared = lab.db.shared_path.read_bytes()
    assert marker.encode() not in shared  # the agent's reply is never handed off
    assert b"dt too large" in shared and b"rerun" in shared  # the diagnosis outcome is
    assert b"hunter2" not in shared
    assert b"OTHER-GROUP-MARKER" not in shared
    assert report["excluded"]["outside this group's projects"] == 1


def test_already_shared_work_is_not_handed_off_twice(lab):
    memory = PrivateMemory(lab.db, "researcher_c")
    draft_id = memory.add_draft("heat-diffusion", {"category": "error_diagnosis", "title": "Already shared one",
                                                   "summary": "Shared earlier.", "verification_status": "hypothesis"})
    assert lab.client.post(f"/drafts/{draft_id}/approve", json={}, headers=lab.headers("researcher_c")).status_code == 200

    report = lab.offboard().json()

    assert report["excluded"]["already shared"] == 1
    assert lab.scalar("SELECT COUNT(*) FROM shared_findings WHERE title = 'Already shared one'") == 1


def test_a_failed_handoff_stores_nothing_and_says_so(lab, monkeypatch):
    def broken_verify(*args):
        raise RuntimeError("simulated storage problem")

    monkeypatch.setattr(offboarding, "_verify", broken_verify)
    report = lab.offboard().json()

    assert report["status"] == "failed"
    assert report["records_transferred"] == 0
    assert "nothing was stored" in report["error"]
    assert not report["steps"][-2]["done"] and not report["steps"][-1]["done"]
    assert lab.scalar("SELECT COUNT(*) FROM shared_findings") == 0
    assert lab.scalar("SELECT status FROM agents WHERE member_id = 'researcher_c'") == "active"
    assert lab.scalar("SELECT status FROM members WHERE id = 'researcher_c'") == "offboarded"  # access stays revoked
    history = lab.client.get("/offboarding", headers=lab.headers("leader")).json()
    assert history[0]["status"] == "failed"


def test_a_failed_handoff_can_be_retried(lab, monkeypatch):
    monkeypatch.setattr(offboarding, "_verify", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert lab.offboard().json()["status"] == "failed"
    monkeypatch.undo()

    retry = lab.offboard().json()

    assert retry["status"] == "completed"
    assert retry["records_transferred"] == 3


def test_a_member_cant_be_offboarded_twice(lab):
    assert lab.offboard().status_code == 200
    assert lab.offboard().status_code == 409


def test_the_policy_is_published_and_members_are_listed_for_the_leader(lab):
    policy = lab.client.get("/policy").json()["offboarding"]
    lab.offboard()

    members = lab.client.get("/members", headers=lab.headers("leader")).json()

    assert "Personal notes" in policy and "never handed off" in policy
    assert {m["id"]: m["status"] for m in members}["researcher_c"] == "offboarded"
    assert lab.client.get("/members", headers=lab.headers("researcher_a")).status_code == 403
