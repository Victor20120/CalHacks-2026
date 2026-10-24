"""Tests for watched simulation runs: the step numbers read from the log, the run's
alerts, who may see them (the researcher and the group leader only), and the
connector sending them. No network and no CREAO."""
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.db.database import Database
from backend.monitoring import runs
from backend.workspaces.store import create_project
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import BackendPoster, create_service
from local_connector.simulation_monitor.checks import sample_of, scan_lines
from local_connector.simulation_monitor.watcher import LogWatcher

FIXTURES = Path(__file__).parent / "fixtures"
UNSTABLE_LOG = (FIXTURES / "sample_project" / "run.log").read_text().splitlines()
ALERT = {"severity": "critical", "check": "nan_inf", "message": "max|T| became inf at step 2223 (line 24)",
         "metric": "max|T|", "line_number": 24, "line": "step  2223  max|T| = inf", "step": 2223}


# --- Reading step numbers from a log --------------------------------------


def test_step_numbers_are_read_from_each_step_line():
    samples = [sample_of(line) for line in UNSTABLE_LOG]

    assert samples[0] is None  # the header has no step
    assert samples[-1] is None  # neither has the error line
    assert (samples[1].step, samples[1].values) == (100, {"max|T|": 276.85, "residual": 3.7102})
    assert (samples[-2].step, samples[-2].values) == (2223, {"max|T|": None, "residual": None})  # Inf


def test_step_counters_are_not_plotted_as_metrics():
    assert sample_of("iter = 12  step = 12  energy = 0.5").values == {"energy": 0.5}
    assert sample_of("it 3 converged") is None


def test_alerts_say_which_step_they_came_from():
    by_check = {(a.check, a.metric): a.step for a in scan_lines(UNSTABLE_LOG)}

    assert by_check[("sudden_jump", "max|T|")] == 200
    assert by_check[("overflow", "max|T|")] == 800
    assert by_check[("error_line", None)] == 2223


def test_the_watcher_hands_over_step_numbers_only_when_asked(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("\n".join(UNSTABLE_LOG[:4]) + "\n")
    samples = []
    LogWatcher(log, lambda alerts: None, on_samples=samples.extend).poll_once()
    LogWatcher(log, lambda alerts: None).poll_once()  # no on_samples: nothing to hand over, no error

    assert [s.step for s in samples] == [100, 200, 300]


# --- Backend ---------------------------------------------------------------


class Lab:
    def __init__(self, tmp_path):
        self.data = tmp_path / "data"
        self.client = TestClient(create_app(client_factory=lambda prefix: None, data_dir=self.data))
        self._headers = {}

    def headers(self, member):
        if member not in self._headers:
            token = self.client.post("/sign-in", json={"member_id": member}).json()["token"]
            self._headers[member] = {"Authorization": f"Bearer {token}"}
        return self._headers[member]

    def share(self, member, project="heat-diffusion", label="live.log"):
        return self.client.post("/simulation-runs", json={"project_id": project, "run_label": label},
                                headers=self.headers(member))

    def report(self, member, run_id, points=(), alerts=(), stopped=False):
        body = {"points": list(points), "alerts": list(alerts), "stopped": stopped}
        return self.client.post(f"/simulation-runs/{run_id}/report", json=body, headers=self.headers(member))

    def runs(self, member, **params):
        return self.client.get("/simulation-runs", params=params, headers=self.headers(member))

    def run(self, member, run_id):
        return self.client.get(f"/simulation-runs/{run_id}", headers=self.headers(member))


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


POINTS = [{"step": 100, "values": {"max|T|": 276.85, "residual": 3.7}},
          {"step": 200, "values": {"max|T|": 2.75e14, "residual": 4.7e14}},
          {"step": 2223, "values": {"max|T|": None, "residual": None}}]


def test_the_leader_sees_a_shared_run_with_its_numbers_and_alerts(lab):
    run = lab.share("researcher_a").json()
    lab.report("researcher_a", run["id"], POINTS, [ALERT])

    [listed] = lab.runs("leader").json()
    detail = lab.run("leader", run["id"]).json()

    assert (listed["member_name"], listed["project_id"], listed["run_label"]) == ("Researcher A", "heat-diffusion", "live.log")
    assert (listed["critical"], listed["warning"], listed["last_step"], listed["watching"]) == (1, 0, 2223, True)
    assert listed["metrics"] == ["max|T|", "residual"]
    assert detail["series"]["max|T|"] == [[100, 276.85], [200, 2.75e14], [2223, None]]
    assert [(a["check"], a["step"]) for a in detail["alerts"]] == [("nan_inf", 2223)]


def test_other_researchers_never_see_a_shared_run(lab):
    run = lab.share("researcher_a").json()
    lab.report("researcher_a", run["id"], POINTS, [ALERT])

    assert lab.runs("researcher_b").json() == []
    assert lab.run("researcher_b", run["id"]).status_code == 404  # looks like it doesn't exist
    assert lab.client.get("/simulation-runs/alerts", headers=lab.headers("researcher_b")).json() == []
    assert [r["id"] for r in lab.runs("researcher_a").json()] == [run["id"]]  # the researcher sees their own


def test_the_leader_only_sees_runs_in_their_own_projects(lab):
    lab.headers("researcher_a")  # signing in creates the demo databases
    with closing(Database(lab.data).shared()) as conn:
        create_project(conn, "demo-lab", "solo-study", "Solo study", "", ["researcher_a"])
    hidden = lab.share("researcher_a", project="solo-study").json()
    shown = lab.share("researcher_a", project="wave-propagation").json()

    assert [r["id"] for r in lab.runs("leader").json()] == [shown["id"]]
    assert lab.run("leader", hidden["id"]).status_code == 404
    assert lab.runs("leader", project_id="heat-diffusion").json() == []


def test_only_project_members_can_share_and_only_the_owner_can_report(lab):
    run = lab.share("researcher_a").json()

    assert lab.share("researcher_b", project="wave-propagation").status_code == 403
    assert lab.report("researcher_b", run["id"], POINTS).status_code == 404
    assert lab.report("leader", run["id"], POINTS).status_code == 404
    assert lab.client.post("/simulation-runs", json={"project_id": "heat-diffusion", "run_label": "x"}).status_code == 401


def test_a_bad_alert_stores_nothing(lab):
    run = lab.share("researcher_a").json()

    refused = lab.report("researcher_a", run["id"], POINTS, [{**ALERT, "severity": "panic"}])

    assert refused.status_code == 422
    assert lab.run("leader", run["id"]).json()["series"] == {}


def test_stopping_the_watch_is_shown(lab):
    run = lab.share("researcher_a").json()
    lab.report("researcher_a", run["id"], stopped=True)

    assert lab.runs("leader").json()[0]["watching"] is False


def test_recent_alerts_name_the_researcher_and_run(lab):
    run = lab.share("researcher_a").json()
    lab.report("researcher_a", run["id"], alerts=[{**ALERT, "severity": "warning", "check": "sudden_jump"}, ALERT])

    alerts = lab.client.get("/simulation-runs/alerts", headers=lab.headers("leader")).json()

    assert [(a["severity"], a["member_name"], a["run_label"]) for a in alerts] == [
        ("critical", "Researcher A", "live.log"), ("warning", "Researcher A", "live.log")]


def test_secrets_in_shared_alert_lines_are_blanked(lab):
    run = lab.share("researcher_a").json()
    lab.report("researcher_a", run["id"], alerts=[{**ALERT, "line": "token = abc123secret max|T| = inf"}])

    assert "abc123secret" not in lab.run("leader", run["id"]).text


def test_a_run_keeps_at_most_six_metrics(lab):
    run = lab.share("researcher_a").json()
    lab.report("researcher_a", run["id"], [{"step": 1, "values": {f"m{i}": float(i) for i in range(6)}}])
    lab.report("researcher_a", run["id"], [{"step": 2, "values": {"m0": 1.0, "extra": 9.0}}])

    assert lab.run("leader", run["id"]).json()["metrics"] == [f"m{i}" for i in range(6)]


def test_long_runs_are_thinned_but_keep_the_end_and_every_blow_up(lab, monkeypatch):
    monkeypatch.setattr(runs, "MAX_POINTS_PER_METRIC", 10)
    run = lab.share("researcher_a").json()
    points = [{"step": s, "values": {"x": None if s == 3 else float(s)}} for s in range(1, 13)]
    lab.report("researcher_a", run["id"], points)

    series = lab.run("leader", run["id"]).json()["series"]["x"]

    assert len(series) < 12
    assert [3, None] in series and series[0] == [1, 1.0] and series[-1] == [12, 12.0]


# --- Connector -> backend --------------------------------------------------


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "heat_project"
    root.mkdir()
    (root / "live.log").write_text("")
    return root


@pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")  # TestClient stands in for requests
def test_a_watched_run_shows_live_for_the_researcher_and_the_leader(lab, project, tmp_path):
    registry = GrantRegistry(state_file=tmp_path / "grants.json")
    service = TestClient(create_service(registry, backend_url="http://testserver", http=lab.client,
                                        watcher_factory=lambda path, poster: LogWatcher(path, poster, poll_seconds=0.05)))
    service.post("/grants", json={"root": str(project)})

    started = service.post("/watches", json={"root": str(project), "file": "live.log", "project_id": "heat-diffusion",
                                             "member_id": "researcher_a"}).json()
    listed_at_start = [r["id"] for r in lab.runs("researcher_a").json()]
    (project / "live.log").write_text("\n".join(UNSTABLE_LOG[:10]) + "\n")
    for _ in range(60):
        if lab.run("leader", started["run_id"]).json()["alerts"]:
            break
        time.sleep(0.05)
    service.delete(f"/watches/{started['id']}")

    assert listed_at_start == [started["run_id"]]  # on the dashboard before the log has any numbers
    detail = lab.run("leader", started["run_id"]).json()
    assert [s for s, _ in detail["series"]["max|T|"]] == list(range(100, 1000, 100))
    assert {a["check"] for a in detail["alerts"]} >= {"sudden_jump", "overflow"}
    assert lab.runs("leader").json()[0]["watching"] is False  # the researcher stopped watching
    assert lab.runs("researcher_b").json() == []


@pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")  # TestClient stands in for requests
def test_a_rerun_into_the_same_log_becomes_a_new_run(lab, tmp_path):
    class Http:
        def post(self, url, **kwargs):
            return lab.client.post(url.removeprefix("http://testserver"), json=kwargs["json"], headers=kwargs.get("headers"))

    log = tmp_path / "live.log"
    poster = BackendPoster("http://testserver", "researcher_a", "heat-diffusion", "live.log", Http())
    watcher = LogWatcher(log, poster, on_samples=poster.samples, on_restart=poster.new_run)
    log.write_text("\n".join(UNSTABLE_LOG[:12]) + "\n")
    watcher.poll_once()
    log.write_text("\n".join(UNSTABLE_LOG[:3]) + "\n")  # the solver started again: the log is overwritten
    watcher.poll_once()

    newest, first = lab.runs("leader").json()
    assert (first["last_step"], first["watching"]) == (1100, False)
    assert newest["last_step"] == 200 and newest["warning"] == 2  # not added to the first run's counts


def test_step_numbers_wait_while_the_backend_is_down(lab):
    class Flaky:
        up = False

        def post(self, url, **kwargs):
            if not self.up:
                raise ConnectionError("backend is down")
            return lab.client.post(url.removeprefix("http://testserver"), json=kwargs["json"],
                                   headers=kwargs.get("headers"))

    http = Flaky()
    poster = BackendPoster("http://testserver", "researcher_a", "heat-diffusion", "live.log", http)

    poster.samples([sample_of(UNSTABLE_LOG[1])])
    http.up = True
    poster.samples([sample_of(UNSTABLE_LOG[2])])

    [run] = lab.runs("leader").json()
    assert [s for s, _ in lab.run("leader", run["id"]).json()["series"]["max|T|"]] == [100, 200]
