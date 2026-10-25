"""Tests for the diagnosis API. CREAO is faked, so no network or credits."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import CreaoTimeout, parse_run
from backend.orchestration.diagnosis import EvidenceRejected, prepare_diagnosis
from backend.orchestration.evidence_policy import MAX_ITEM_CHARS
from local_connector.evidence.extract import extract_evidence
from local_connector.file_access.grants import GrantRegistry

FIXTURES = Path(__file__).parent / "fixtures"
WITH_ISSUES = json.loads((FIXTURES / "creao_hub_run_with_issue_list.json").read_text(encoding="utf-8"))
LOG_EXCERPT = "    1| heat2d: grid 41x41, dx=0.01, dt=0.03\n   25| ERROR: non-finite temperature at step 2223"
GOOD = {
    "project_id": "heat-diffusion",
    "symptom": "max|T| reaches inf at step 2223",
    "evidence": [{"path": "run.log", "kind": "log", "excerpt": LOG_EXCERPT, "truncated": True}],
}


class FakeCreao:
    def __init__(self, result=None, error=None):
        self.result = result or parse_run(WITH_ISSUES["final_response"])
        self.error = error
        self.calls = []

    def run(self, inputs):
        self.calls.append(inputs)
        if self.error:
            raise self.error
        return self.result


class Api:
    """A backend with a fake CREAO that records which personal agent each call used."""

    def __init__(self, tmp_path, fake):
        self.fake = fake
        self.agents_used = []

        def factory(prefix):
            self.agents_used.append(prefix)
            return fake

        self.client = TestClient(create_app(client_factory=factory, data_dir=tmp_path / "data"))
        self._headers = {}

    def headers(self, member="researcher_a"):
        if member not in self._headers:
            token = self.client.post("/sign-in", json={"member_id": member}).json()["token"]
            self._headers[member] = {"Authorization": f"Bearer {token}"}
        return self._headers[member]

    def diagnose(self, body=GOOD, member="researcher_a"):
        return self.client.post("/diagnose", json=body, headers=self.headers(member))

    def job(self, job_id, member="researcher_a"):
        return self.client.get(f"/diagnose/{job_id}", headers=self.headers(member))


@pytest.fixture
def api(tmp_path):
    return Api(tmp_path, FakeCreao())


def with_evidence(path=None, excerpt=None):
    item = {**GOOD["evidence"][0]}
    if path is not None:
        item["path"] = path
    if excerpt is not None:
        item["excerpt"] = excerpt
    return {**GOOD, "evidence": [item]}


# --- Sign-in, projects and agents ------------------------------------------


def test_health(api):
    assert api.client.get("/health").json() == {"status": "ok", "group": "demo"}


def test_diagnosis_needs_sign_in(api):
    assert api.client.post("/diagnose", json=GOOD).status_code == 401
    assert api.fake.calls == []


def test_diagnosis_job_completes_with_issues_and_a_private_draft(api):
    started = api.diagnose()
    job = api.job(started.json()["job_id"]).json()

    assert started.status_code == 202
    assert started.json()["evidence_sent"] == [{"path": "run.log", "chars": len(LOG_EXCERPT)}]
    assert job["status"] == "completed"
    assert len(job["diagnosis"]["issues"]) == 6
    assert job["diagnosis"]["run_id"] == WITH_ISSUES["final_response"]["id"]
    assert job["draft_id"]


def test_each_researcher_uses_their_own_agent(api):
    api.diagnose(member="researcher_a")
    api.diagnose(member="researcher_b")

    assert api.agents_used == ["AGENT_1", "AGENT_2"]


def test_researcher_b_cannot_diagnose_a_project_they_are_not_in(api):
    response = api.diagnose({**GOOD, "project_id": "wave-propagation"}, member="researcher_b")

    assert response.status_code == 403
    assert api.fake.calls == []


def test_member_without_a_personal_agent_is_refused(api):
    assert api.diagnose(member="leader").status_code == 403


def test_other_members_cannot_read_your_diagnosis(api):
    job_id = api.diagnose(member="researcher_a").json()["job_id"]

    assert api.job(job_id, member="researcher_b").status_code == 404


# --- What is sent to CREAO -------------------------------------------------


def test_creao_gets_personal_agent_inputs_and_no_local_paths(api):
    api.diagnose()

    inputs = api.fake.calls[0]
    assert set(inputs) == {"project_path", "observed_symptom", "focus_areas", "analysis_depth"}
    assert inputs["project_path"].startswith("Heat diffusion solver (local files are not attached")
    assert inputs["observed_symptom"].startswith("Response guidelines selected by this user:")
    assert "max|T| reaches inf at step 2223" in inputs["observed_symptom"]
    assert "--- run.log (log, partial) ---" in inputs["observed_symptom"]
    assert inputs["focus_areas"] == "numerical"
    assert inputs["analysis_depth"] == "standard"


def test_focus_areas_and_depth_are_passed_on(api):
    api.diagnose({**GOOD, "focus_areas": ["numerical", "reproducibility"], "analysis_depth": "deep"})

    assert api.fake.calls[0]["focus_areas"] == "numerical, reproducibility"
    assert api.fake.calls[0]["analysis_depth"] == "deep"


@pytest.mark.parametrize("change", [{"focus_areas": ["vibes"]}, {"focus_areas": []}, {"analysis_depth": "extreme"}])
def test_invalid_focus_areas_or_depth_are_refused(api, change):
    assert api.diagnose({**GOOD, **change}).status_code == 422
    assert api.fake.calls == []


@pytest.mark.parametrize("path", [r"C:\Users\me\run.log", "/home/me/run.log", "../secret.txt"])
def test_rejects_local_or_escaping_paths(api, path):
    assert api.diagnose(with_evidence(path=path)).status_code == 422
    assert api.fake.calls == []


@pytest.mark.parametrize("path", [".env", "keys/deploy.pem", ".git/config"])
def test_rejects_sensitive_files(api, path):
    response = api.diagnose(with_evidence(path=path))

    assert response.status_code == 422
    assert "sensitive" in response.json()["detail"]
    assert api.fake.calls == []


def test_rejects_oversized_evidence(api):
    too_long = [{"path": "run.log", "kind": "log", "excerpt": "x" * (MAX_ITEM_CHARS + 1)}]
    too_much = [{"path": f"f{k}.py", "kind": "code", "excerpt": "x" * 3500} for k in range(4)]

    assert api.diagnose({**GOOD, "evidence": too_long}).status_code == 413
    assert api.diagnose({**GOOD, "evidence": too_much}).status_code == 413
    assert api.fake.calls == []


def test_secrets_are_blanked_before_anything_reaches_creao(api):
    excerpt = "    3| key = capi_live_secret_12345\n    4| password = hunter2"

    started = api.diagnose(with_evidence(path="notes.txt", excerpt=excerpt))

    sent = json.dumps(api.fake.calls[0])
    assert "capi_live_secret_12345" not in sent
    assert "hunter2" not in sent
    assert started.json()["redactions"] == 2


def test_a_project_name_that_looks_like_a_path_is_refused():
    with pytest.raises(EvidenceRejected):
        prepare_diagnosis(r"C:\Users\me\heat_project", "blows up", ["numerical"], "standard", [])


# --- Job outcomes ----------------------------------------------------------


def test_creao_timeout_marks_the_job_failed_with_the_run_id(tmp_path):
    api = Api(tmp_path, FakeCreao(error=CreaoTimeout("run-123", 300)))

    job = api.job(api.diagnose().json()["job_id"]).json()

    assert job["status"] == "failed"
    assert job["error"]["type"] == "timeout"
    assert job["error"]["run_id"] == "run-123"
    assert job["draft_id"] is None


def test_unknown_job_is_404(api):
    assert api.job("no-such-job").status_code == 404


def test_connector_evidence_is_accepted_by_the_backend(api, tmp_path):
    """The local connector's output matches what the backend expects."""
    project = tmp_path / "heat_project"
    project.mkdir()
    for name in ("heat2d.py", "config.json", "run.log"):
        (project / name).write_text((FIXTURES / "sample_project" / name).read_text(encoding="utf-8"), encoding="utf-8")
    registry = GrantRegistry(state_file=tmp_path / "grants.json")
    evidence = extract_evidence(registry, registry.grant(project), ["run.log", "config.json", "heat2d.py:18-24"])

    started = api.diagnose({"project_id": "heat-diffusion", "symptom": "blows up", "evidence": evidence.to_payload()})
    job = api.job(started.json()["job_id"]).json()

    assert started.status_code == 202
    assert job["status"] == "completed"
    assert str(tmp_path) not in json.dumps(api.fake.calls[0])
