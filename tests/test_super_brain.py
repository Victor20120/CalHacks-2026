"""Tests for Super Brain lookup: search, links, and earlier findings as diagnosis
context. Every result must respect project access. CREAO is faked."""
import json
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.auth.permissions import context_project_ids
from backend.creao import parse_run
from backend.db.database import Database
from backend.memory.shared_findings import _terms
from backend.orchestration.diagnosis import MAX_CONTEXT_CHARS, MAX_CONTEXT_FINDINGS

FIXTURES = Path(__file__).parent / "fixtures"
RESULT = parse_run(json.loads((FIXTURES / "creao_personal_agent_diagnosis.json").read_text(encoding="utf-8"))["final_response"])

HEAT_TITLE = "Explicit time step above the CFL limit makes the heat solver blow up"
HEAT_SUMMARY = "Symptom: max|T| reaches inf. Cause: dt = 0.03 gives r = 0.30 > 0.25. Use dt <= 0.02 when dx = 0.01."
WAVE_TITLE = "Wave solver blows up when the Courant number exceeds 1"
WAVE_SUMMARY = "Restricted wave project detail: c dt / dx = 1.2 in the leapfrog scheme makes max|u| reach inf."


class FakeCreao:
    def __init__(self):
        self.calls = []

    def run(self, inputs):
        self.calls.append(inputs)
        return RESULT


class Lab:
    def __init__(self, tmp_path):
        data = tmp_path / "data"
        self.fake = FakeCreao()
        self.client = TestClient(create_app(client_factory=lambda prefix: self.fake, data_dir=data))
        self.db = Database(data)
        self._headers = {}

    def headers(self, member):
        if member not in self._headers:
            token = self.client.post("/sign-in", json={"member_id": member}).json()["token"]
            self._headers[member] = {"Authorization": f"Bearer {token}"}
        return self._headers[member]

    def diagnose(self, member, project, symptom, use_super_brain=True):
        body = {"project_id": project, "symptom": symptom, "use_super_brain": use_super_brain}
        started = self.client.post("/diagnose", json=body, headers=self.headers(member)).json()
        job = self.client.get(f"/diagnose/{started['job_id']}", headers=self.headers(member)).json()
        return started, job

    def approve(self, member, draft_id, **edits):
        return self.client.post(f"/drafts/{draft_id}/approve", json=edits, headers=self.headers(member)).json()

    def publish(self, member, project, title, summary, symptom="the run blows up", use_super_brain=False):
        """Diagnose, then approve the draft with the given title and summary."""
        _, job = self.diagnose(member, project, symptom, use_super_brain)
        return self.approve(member, job["draft_id"], title=title, summary=summary)

    def search(self, member, q, project_id=None):
        params = {"q": q, **({"project_id": project_id} if project_id else {})}
        return self.client.get("/superbrain/search", params=params, headers=self.headers(member))

    def finding(self, member, finding_id):
        return self.client.get(f"/findings/{finding_id}", headers=self.headers(member))


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


# --- Search ----------------------------------------------------------------


def test_b_retrieves_a_approved_finding_from_their_shared_project(lab):
    heat = lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)

    results = lab.search("researcher_b", "heat solver blows up, max|T| reaches inf").json()

    assert [r["id"] for r in results] == [heat["id"]]
    assert results[0]["contributed_by_name"] == "Researcher A"
    assert results[0]["verification_status"] == "hypothesis"


def test_b_cannot_retrieve_findings_from_a_project_they_are_not_in(lab):
    wave = lab.publish("researcher_a", "wave-propagation", WAVE_TITLE, WAVE_SUMMARY)

    assert lab.search("researcher_b", "wave solver blows up courant").json() == []
    assert lab.search("researcher_b", "wave", project_id="wave-propagation").status_code == 403
    assert lab.finding("researcher_b", wave["id"]).status_code == 404  # looks like it doesn't exist
    assert [r["id"] for r in lab.search("researcher_a", "wave solver blows up courant").json()] == [wave["id"]]


def test_links_never_lead_into_a_restricted_project(lab):
    heat = lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)
    wave = lab.publish("researcher_a", "wave-propagation", WAVE_TITLE, WAVE_SUMMARY)
    with closing(lab.db.shared()) as conn:  # any link from heat to wave, however it was made
        conn.execute("INSERT INTO finding_links VALUES (?, ?, 'similar_to', 'now')", (heat["id"], wave["id"]))
        conn.commit()

    seen_by_a = lab.finding("researcher_a", heat["id"]).json()
    seen_by_b = lab.finding("researcher_b", heat["id"])

    assert [link["finding"]["id"] for link in seen_by_a["related"]] == [wave["id"]]
    assert seen_by_b.status_code == 200
    assert seen_by_b.json()["related"] == []
    assert wave["id"] not in seen_by_b.text and "Restricted wave project" not in seen_by_b.text


def test_unapproved_or_rejected_drafts_are_never_returned(lab):
    _, pending = lab.diagnose("researcher_a", "heat-diffusion", "unique-pending-marker blows up")
    _, rejected = lab.diagnose("researcher_a", "heat-diffusion", "unique-rejected-marker blows up")
    lab.client.post(f"/drafts/{rejected['draft_id']}/reject", headers=lab.headers("researcher_a"))

    for member in ("researcher_a", "researcher_b"):
        assert lab.search(member, "unique-pending-marker blows up").json() == []
        assert lab.search(member, "unique-rejected-marker blows up").json() == []


def test_search_ranks_the_better_match_first(lab):
    lab.publish("researcher_a", "heat-diffusion", "Boundary values frozen at their initial state", "Edges never update.")
    heat = lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)

    results = lab.search("researcher_b", "CFL limit time step heat solver").json()

    assert results[0]["id"] == heat["id"]
    assert lab.search("researcher_b", "completely unrelated words").json() == []


def test_search_needs_sign_in_and_a_real_query(lab):
    assert lab.client.get("/superbrain/search", params={"q": "cfl"}).status_code == 401
    assert lab.search("researcher_b", "x").status_code == 422


def test_query_terms_keep_numbers_and_short_symbols():
    assert {"dt", "0.03", "cfl", "inf"} <= _terms("The dt = 0.03 run hits the CFL limit and reaches inf.")
    assert "the" not in _terms("the and of")


# --- Earlier findings as diagnosis context ---------------------------------


def test_diagnosis_gives_the_agent_relevant_earlier_findings(lab):
    heat = lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)

    started, job = lab.diagnose("researcher_b", "heat-diffusion", "After refining the grid the heat solver blows up to inf")

    context = lab.fake.calls[-1]["known_context"]
    assert HEAT_TITLE in context and "Use dt <= 0.02" in context
    assert "[hypothesis]" in context and "Researcher A" in context
    assert [f["id"] for f in started["prior_findings"]] == [heat["id"]]
    assert [f["id"] for f in job["prior_findings"]] == [heat["id"]]


def test_super_brain_lookup_can_be_turned_off(lab):
    lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)

    started, _ = lab.diagnose("researcher_b", "heat-diffusion", "heat solver blows up to inf", use_super_brain=False)

    assert "known_context" not in lab.fake.calls[-1]
    assert started["prior_findings"] == []


def test_diagnosis_context_only_uses_findings_everyone_in_the_project_can_read(lab):
    lab.publish("researcher_a", "wave-propagation", WAVE_TITLE, WAVE_SUMMARY)

    # Even A, who can read the wave project, doesn't get wave findings in a heat diagnosis:
    # the result could become a heat finding that B reads.
    lab.diagnose("researcher_a", "heat-diffusion", "solver blows up, max reaches inf")

    assert "known_context" not in lab.fake.calls[-1]


def test_wave_diagnosis_may_use_heat_findings(lab):
    lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)

    lab.diagnose("researcher_a", "wave-propagation", "wave solver blows up to inf like the heat solver")

    assert HEAT_TITLE in lab.fake.calls[-1]["known_context"]


def test_context_projects_follow_membership(lab):
    lab.search("researcher_a", "warm up the database")
    with closing(lab.db.shared()) as conn:
        assert context_project_ids(conn, "heat-diffusion") == ["heat-diffusion"]
        assert context_project_ids(conn, "wave-propagation") == ["heat-diffusion", "wave-propagation"]


def test_known_context_is_size_limited(lab):
    for k in range(5):
        lab.publish("researcher_a", "heat-diffusion", f"{HEAT_TITLE} ({k})", HEAT_SUMMARY + " x" * 900)

    started, _ = lab.diagnose("researcher_b", "heat-diffusion", "heat solver blows up to inf")

    assert len(lab.fake.calls[-1]["known_context"]) <= MAX_CONTEXT_CHARS
    assert 1 <= len(started["prior_findings"]) <= MAX_CONTEXT_FINDINGS


# --- Links from approved knowledge -----------------------------------------


def test_approving_b_draft_links_it_to_the_findings_its_diagnosis_used(lab):
    heat = lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)
    _, job = lab.diagnose("researcher_b", "heat-diffusion", "After refining the grid the heat solver blows up to inf")

    b_finding = lab.approve("researcher_b", job["draft_id"])

    detail = lab.finding("researcher_b", b_finding["id"]).json()
    assert detail["related"] == [{"relation": "associated_with", "direction": "outgoing",
                                  "finding": lab.finding("researcher_b", heat["id"]).json()["finding"]}]
    back = lab.finding("researcher_a", heat["id"]).json()["related"]
    assert [(link["direction"], link["finding"]["id"]) for link in back] == [("incoming", b_finding["id"])]


def test_a_diagnosis_without_context_creates_no_links(lab):
    finding = lab.publish("researcher_a", "heat-diffusion", HEAT_TITLE, HEAT_SUMMARY)

    assert lab.finding("researcher_a", finding["id"]).json()["related"] == []
