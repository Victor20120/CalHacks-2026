"""Tests for drafts, approval and publishing to the Super Brain. CREAO is faked."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import parse_run
from backend.db.database import Database, OutdatedDatabase
from backend.knowledge_sharing.publishing import draft_from_diagnosis
from backend.memory.private_memory import PrivateMemory

FIXTURES = Path(__file__).parent / "fixtures"
PERSONAL = json.loads((FIXTURES / "creao_personal_agent_diagnosis.json").read_text(encoding="utf-8"))
RESULT = parse_run(PERSONAL["final_response"])
LOG_EXCERPT = "    1| heat2d: grid 41x41, dx=0.01, dt=0.03\n   25| ERROR: non-finite temperature at step 2223"
DIAGNOSE = {
    "project_id": "heat-diffusion",
    "symptom": "max|T| reaches inf at step 2223",
    "evidence": [{"path": "run.log", "kind": "log", "excerpt": LOG_EXCERPT, "truncated": True}],
}


class FakeCreao:
    def run(self, inputs):
        return RESULT


class Lab:
    """A backend with a fake CREAO, plus direct access to its databases."""

    def __init__(self, tmp_path):
        data = tmp_path / "data"
        self.client = TestClient(create_app(client_factory=lambda prefix: FakeCreao(), data_dir=data))
        self.db = Database(data)
        self._headers = {}

    def headers(self, member):
        if member not in self._headers:
            token = self.client.post("/sign-in", json={"member_id": member}).json()["token"]
            self._headers[member] = {"Authorization": f"Bearer {token}"}
        return self._headers[member]

    def diagnose(self, member="researcher_a"):
        job_id = self.client.post("/diagnose", json=DIAGNOSE, headers=self.headers(member)).json()["job_id"]
        return self.client.get(f"/diagnose/{job_id}", headers=self.headers(member)).json()

    def drafts(self, member="researcher_a"):
        return self.client.get("/drafts", headers=self.headers(member)).json()

    def approve(self, draft_id, member="researcher_a", **edits):
        return self.client.post(f"/drafts/{draft_id}/approve", json=edits, headers=self.headers(member))

    def reject(self, draft_id, member="researcher_a"):
        return self.client.post(f"/drafts/{draft_id}/reject", headers=self.headers(member))

    def findings(self, project="heat-diffusion", member="researcher_a"):
        return self.client.get(f"/projects/{project}/findings", headers=self.headers(member))

    def count(self, table):
        with closing(self.db.shared()) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


# --- Diagnosis -> private memory -> draft ----------------------------------


def test_a_diagnosis_is_saved_privately_and_nothing_is_shared(lab):
    lab.diagnose()

    episodes = PrivateMemory(lab.db, "researcher_a").episodes("heat-diffusion")
    assert len(episodes) == 1
    assert episodes[0]["kind"] == "diagnosis"
    assert episodes[0]["status"] == "hypothesis"
    assert episodes[0]["creao_run_id"] == RESULT.run_id
    assert lab.count("shared_findings") == 0
    assert lab.count("approvals") == 0


def test_the_draft_is_a_minimum_necessary_hypothesis(lab):
    lab.diagnose()

    [draft] = lab.drafts()
    top = next(i for i in RESULT.issues if i["status"] == "confirmed")
    assert draft["category"] == "error_diagnosis"
    assert draft["verification_status"] == "hypothesis"
    assert draft["title"] == top["title"]
    assert "How to validate:" in draft["summary"]
    assert "Not yet verified by a researcher." in draft["summary"]
    assert "   25| ERROR" not in draft["summary"]  # no raw evidence excerpts
    assert RESULT.issues[1]["title"] not in draft["summary"]  # only the top issue


def test_no_draft_when_the_agent_found_nothing():
    assert draft_from_diagnosis("x", {"analysis_ran": False, "issues": RESULT.issues}) is None
    assert draft_from_diagnosis("x", {"analysis_ran": True, "issues": []}) is None


def test_drafts_are_private_to_their_author(lab):
    lab.diagnose(member="researcher_a")
    [draft] = lab.drafts("researcher_a")

    assert lab.drafts("researcher_b") == []
    assert lab.approve(draft["id"], member="researcher_b").status_code == 404
    assert lab.count("shared_findings") == 0


# --- Approving -------------------------------------------------------------


def test_approving_publishes_the_finding_with_provenance(lab):
    lab.diagnose()
    [draft] = lab.drafts()

    response = lab.approve(draft["id"])

    finding = response.json()
    assert response.status_code == 200
    assert finding["project_id"] == "heat-diffusion"
    assert finding["contributed_by"] == "researcher_a"
    assert finding["source_creao_run_id"] == RESULT.run_id
    assert finding["edited_before_approval"] is False
    assert lab.drafts() == []  # no longer pending
    assert [f["id"] for f in lab.findings(member="researcher_b").json()] == [finding["id"]]  # B is in this project


def test_a_hypothesis_stays_a_hypothesis_after_publishing(lab):
    lab.diagnose()
    [draft] = lab.drafts()

    lab.approve(draft["id"])

    with closing(lab.db.shared()) as conn:
        assert conn.execute("SELECT verification_status FROM shared_findings").fetchone()[0] == "hypothesis"


def test_edits_before_approval_are_kept_and_marked(lab):
    lab.diagnose()
    [draft] = lab.drafts()

    finding = lab.approve(draft["id"], title="dt = 0.03 breaks the explicit stability limit",
                          summary="Use dt <= 0.02 for this grid.", verification_status="supported").json()

    assert finding["title"] == "dt = 0.03 breaks the explicit stability limit"
    assert finding["summary"] == "Use dt <= 0.02 for this grid."
    assert finding["verification_status"] == "supported"
    assert finding["edited_before_approval"] is True


def test_nothing_is_shared_without_an_approval_record(lab):
    lab.diagnose()
    [draft] = lab.drafts()
    lab.approve(draft["id"])

    with closing(lab.db.shared()) as conn:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM shared_findings f LEFT JOIN approvals a ON a.id = f.approval_id WHERE a.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO shared_findings VALUES ('f-x', 'demo-lab', 'heat-diffusion', 'error_diagnosis',
                   't', 's', 'hypothesis', 'researcher_a', 'no-such-approval', NULL, 'now')"""
            )


def test_a_draft_can_only_be_decided_once(lab):
    lab.diagnose()
    [draft] = lab.drafts()

    assert lab.approve(draft["id"]).status_code == 200
    assert lab.approve(draft["id"]).status_code == 409
    assert lab.reject(draft["id"]).status_code == 409
    assert lab.count("shared_findings") == 1


@pytest.mark.parametrize("edits", [{"verification_status": "proven"}, {"category": "gossip"}, {"title": "  "}])
def test_invalid_edits_are_refused(lab, edits):
    lab.diagnose()
    [draft] = lab.drafts()

    assert lab.approve(draft["id"], **edits).status_code == 422
    assert lab.count("shared_findings") == 0


def test_secrets_in_edits_are_blanked_before_sharing(lab):
    lab.diagnose()
    [draft] = lab.drafts()

    finding = lab.approve(draft["id"], summary="Fixed it. password = hunter2").json()

    assert "hunter2" not in finding["summary"]


# --- Rejecting -------------------------------------------------------------


def test_rejected_drafts_never_reach_the_shared_database(lab):
    lab.diagnose()
    [draft] = lab.drafts()

    response = lab.reject(draft["id"])

    assert response.status_code == 200
    assert lab.count("shared_findings") == 0
    assert lab.count("approvals") == 0
    shared_bytes = lab.db.shared_path.read_bytes()
    assert draft["title"].encode() not in shared_bytes
    assert draft["summary"][:60].encode() not in shared_bytes
    assert len(PrivateMemory(lab.db, "researcher_a").drafts(status="rejected")) == 1


# --- Project access --------------------------------------------------------


def test_findings_respect_project_membership(lab):
    assert lab.findings(project="wave-propagation", member="researcher_b").status_code == 403
    assert lab.findings(project="wave-propagation", member="researcher_a").status_code == 200


def test_leaving_a_project_blocks_publishing_its_drafts(lab):
    lab.diagnose()
    [draft] = lab.drafts()
    with closing(lab.db.shared()) as conn:
        conn.execute("DELETE FROM project_members WHERE member_id = 'researcher_a' AND project_id = 'heat-diffusion'")
        conn.commit()

    assert lab.approve(draft["id"]).status_code == 403
    assert lab.count("shared_findings") == 0


# --- Database versions -----------------------------------------------------


def test_an_old_database_is_refused_with_reset_instructions(tmp_path):
    old = tmp_path / "superbrain.db"
    with closing(sqlite3.connect(old)) as conn:
        conn.execute("CREATE TABLE workspaces (id TEXT)")
        conn.commit()

    with pytest.raises(OutdatedDatabase, match="--reset"):
        Database(tmp_path).shared()
