"""Tests for the diagnosis API. CREAO is faked, so no network or credits."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import CreaoTimeout, parse_run
from backend.orchestration.evidence_policy import MAX_ITEM_CHARS
from local_connector.evidence.extract import extract_evidence
from local_connector.file_access.grants import GrantRegistry

FIXTURES = Path(__file__).parent / "fixtures"
WITH_ISSUES = json.loads((FIXTURES / "creao_hub_run_with_issue_list.json").read_text(encoding="utf-8"))
LOG_EXCERPT = "    1| heat2d: grid 41x41, dx=0.01, dt=0.03\n   25| ERROR: non-finite temperature at step 2223"
GOOD = {
    "project": "heat_project",
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


def client_for(fake):
    return TestClient(create_app(client_factory=lambda: fake))


def test_health():
    assert client_for(FakeCreao()).get("/health").json() == {"status": "ok"}


def test_diagnosis_job_completes_with_issues():
    client = client_for(FakeCreao())

    started = client.post("/diagnose", json=GOOD)
    job = client.get(f"/diagnose/{started.json()['job_id']}").json()

    assert started.status_code == 202
    assert started.json()["evidence_sent"] == [{"path": "run.log", "chars": len(LOG_EXCERPT)}]
    assert job["status"] == "completed"
    assert len(job["diagnosis"]["issues"]) == 6
    assert job["diagnosis"]["run_id"] == WITH_ISSUES["final_response"]["id"]


def test_creao_gets_the_evidence_but_no_local_paths():
    fake = FakeCreao()

    client_for(fake).post("/diagnose", json=GOOD)

    inputs = fake.calls[0]
    assert set(inputs) == {"project_path", "observed_symptom", "focus_areas"}
    assert inputs["project_path"].startswith("heat_project (local files are not attached")
    assert inputs["observed_symptom"].startswith("max|T| reaches inf at step 2223")
    assert "--- run.log (log, partial) ---" in inputs["observed_symptom"]
    assert "ERROR: non-finite temperature" in inputs["observed_symptom"]
    assert inputs["focus_areas"]  # default used when none is given


@pytest.mark.parametrize("path", [r"C:\Users\me\run.log", "/home/me/run.log", "../secret.txt"])
def test_rejects_local_or_escaping_paths(path):
    fake = FakeCreao()
    body = {**GOOD, "evidence": [{**GOOD["evidence"][0], "path": path}]}

    response = client_for(fake).post("/diagnose", json=body)

    assert response.status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize("path", [".env", "keys/deploy.pem", ".git/config"])
def test_rejects_sensitive_files(path):
    fake = FakeCreao()
    body = {**GOOD, "evidence": [{**GOOD["evidence"][0], "path": path}]}

    response = client_for(fake).post("/diagnose", json=body)

    assert response.status_code == 422
    assert "sensitive" in response.json()["detail"]
    assert fake.calls == []


def test_rejects_a_project_given_as_a_path():
    response = client_for(FakeCreao()).post("/diagnose", json={**GOOD, "project": r"C:\Users\me\heat_project"})

    assert response.status_code == 422


def test_rejects_oversized_evidence():
    fake = FakeCreao()
    too_long = [{"path": "run.log", "kind": "log", "excerpt": "x" * (MAX_ITEM_CHARS + 1)}]
    too_much = [{"path": f"f{k}.py", "kind": "code", "excerpt": "x" * 3500} for k in range(4)]
    client = client_for(fake)

    assert client.post("/diagnose", json={**GOOD, "evidence": too_long}).status_code == 413
    assert client.post("/diagnose", json={**GOOD, "evidence": too_much}).status_code == 413
    assert fake.calls == []


def test_secrets_are_blanked_before_anything_reaches_creao():
    fake = FakeCreao()
    excerpt = "    3| key = capi_live_secret_12345\n    4| password = hunter2"
    body = {**GOOD, "evidence": [{"path": "notes.txt", "kind": "log", "excerpt": excerpt}]}

    started = client_for(fake).post("/diagnose", json=body)

    sent = json.dumps(fake.calls[0])
    assert "capi_live_secret_12345" not in sent
    assert "hunter2" not in sent
    assert started.json()["redactions"] == 2


def test_creao_timeout_marks_the_job_failed_with_the_run_id():
    client = client_for(FakeCreao(error=CreaoTimeout("run-123", 300)))

    job_id = client.post("/diagnose", json=GOOD).json()["job_id"]
    job = client.get(f"/diagnose/{job_id}").json()

    assert job["status"] == "failed"
    assert job["error"]["type"] == "timeout"
    assert job["error"]["run_id"] == "run-123"


def test_unknown_job_is_404():
    assert client_for(FakeCreao()).get("/diagnose/no-such-job").status_code == 404


def test_connector_evidence_is_accepted_by_the_backend(tmp_path):
    """The local connector's output matches what the backend expects."""
    project = tmp_path / "heat_project"
    project.mkdir()
    for name in ("heat2d.py", "config.json", "run.log"):
        (project / name).write_text((FIXTURES / "sample_project" / name).read_text(encoding="utf-8"), encoding="utf-8")
    registry = GrantRegistry(state_file=tmp_path / "grants.json")
    evidence = extract_evidence(registry, registry.grant(project), ["run.log", "config.json", "heat2d.py:18-24"])
    fake = FakeCreao()
    client = client_for(fake)

    started = client.post("/diagnose", json={"project": evidence.project, "symptom": "blows up",
                                             "evidence": evidence.to_payload()})
    job = client.get(f"/diagnose/{started.json()['job_id']}").json()

    assert started.status_code == 202
    assert job["status"] == "completed"
    assert str(tmp_path) not in json.dumps(fake.calls[0])
