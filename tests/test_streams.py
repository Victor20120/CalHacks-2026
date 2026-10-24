"""Tests for streamed runs: a VS Code terminal's output sent line by line to the
local connector instead of a log file. Same checks, same reporting to the
backend. No network and no CREAO."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import create_service
from local_connector.simulation_monitor.stream import StreamWatch

FIXTURES = Path(__file__).parent / "fixtures"
UNSTABLE_LOG = (FIXTURES / "sample_project" / "run.log").read_text().splitlines()


class FakePoster:
    def __init__(self):
        self.alerts, self.samples_seen, self.stopped_calls = [], [], 0

    def __call__(self, alerts):
        self.alerts += alerts

    def samples(self, samples):
        self.samples_seen += samples

    def stopped(self):
        self.stopped_calls += 1


def test_streamed_lines_get_the_same_checks_as_a_log_file():
    poster = FakePoster()
    watch = StreamWatch(poster)

    watch.feed(UNSTABLE_LOG[:5])
    watch.feed(UNSTABLE_LOG[5:])

    assert {(a.check, a.step) for a in poster.alerts} >= {("sudden_jump", 200), ("overflow", 800), ("nan_inf", 2223)}
    assert [s.step for s in poster.samples_seen][:3] == [100, 200, 300]
    assert watch.status()["lines_read"] == len(UNSTABLE_LOG)


def test_a_failing_exit_code_is_a_critical_problem_and_ends_the_run():
    poster = FakePoster()
    watch = StreamWatch(poster)
    watch.feed(["step 1 x = 1.0"])

    watch.end(1)
    watch.feed(["step 2 x = 2.0"])  # nothing is read after the end
    watch.end(1)

    assert [(a.severity, a.check) for a in poster.alerts] == [("critical", "exit_code")]
    assert watch.status()["state"] == "finished" and watch.status()["lines_read"] == 1
    assert poster.stopped_calls == 1


def test_a_clean_exit_adds_no_problem():
    poster = FakePoster()
    watch = StreamWatch(poster)
    watch.end(0)
    assert poster.alerts == [] and poster.stopped_calls == 1


@pytest.fixture
def setup(tmp_path):
    backend = TestClient(create_app(client_factory=lambda prefix: None, data_dir=tmp_path / "data"))
    registry = GrantRegistry(state_file=tmp_path / "grants.json")
    service = TestClient(create_service(registry, backend_url="http://testserver", http=backend))
    project = tmp_path / "heat"
    project.mkdir()
    return backend, service, project


def headers(backend, member):
    token = backend.post("/sign-in", json={"member_id": member}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")  # TestClient stands in for requests
def test_a_terminal_run_reaches_the_dashboard_for_the_researcher_and_leader(setup):
    backend, service, project = setup
    service.post("/grants", json={"root": str(project)})
    body = {"root": str(project), "project_id": "heat-diffusion", "member_id": "researcher_a",
            "command": "python heat2d.py config.json --token=abc123secret"}

    started = service.post("/streams", json=body).json()
    service.post(f"/streams/{started['id']}/lines", json={"lines": UNSTABLE_LOG})
    ended = service.post(f"/streams/{started['id']}/end", json={"exit_code": 1}).json()

    [run] = backend.get("/simulation-runs", headers=headers(backend, "leader")).json()
    detail = backend.get(f"/simulation-runs/{run['id']}", headers=headers(backend, "researcher_a")).json()
    assert run["id"] == started["run_id"] and run["watching"] is False
    assert "abc123secret" not in run["run_label"] and run["run_label"].startswith("python heat2d.py config.json")
    assert run["last_step"] == 2223 and "exit_code" in {a["check"] for a in detail["alerts"]}
    assert ended["state"] == "finished" and ended["exit_code"] == 1
    assert backend.get("/simulation-runs", headers=headers(backend, "researcher_b")).json() == []


def test_only_granted_folders_can_stream(setup):
    _, service, project = setup
    body = {"root": str(project), "project_id": "heat-diffusion", "member_id": "researcher_a", "command": "python x.py"}

    assert service.post("/streams", json=body).status_code == 403
    assert service.post("/streams/not-a-stream/lines", json={"lines": ["x"]}).status_code == 404
