"""Tests for the local anomaly checks, the log watcher, the connector service and
the backend's session-only alerts. No network and no CREAO."""
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.db.database import Database
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import BackendPoster, create_service
from local_connector.simulation_monitor.checks import LogMonitor, MonitorConfig, scan_lines
from local_connector.simulation_monitor.watcher import LogWatcher

FIXTURES = Path(__file__).parent / "fixtures"
UNSTABLE_LOG = (FIXTURES / "sample_project" / "run.log").read_text().splitlines()
STABLE_LOG = (FIXTURES / "monitor" / "stable.log").read_text().splitlines()


def kinds(alerts):
    return [(a.severity, a.check, a.metric) for a in alerts]


# --- Checks ----------------------------------------------------------------


@pytest.mark.parametrize("lines, expected", [
    (["step 10 max|T| = nan residual = 1e-3"], ("critical", "nan_inf", "max|T|")),
    (["NaN detected in the velocity field"], ("critical", "nan_inf", None)),
    (["step 5 energy = 3.2e+120"], ("critical", "overflow", "energy")),
    (["ERROR: linear solver failed to converge"], ("critical", "error_line", None)),
    (["Traceback (most recent call last):"], ("critical", "error_line", None)),
    (["step 1 force = 2.0", "step 2 force = 500.0"], ("warning", "sudden_jump", "force")),
    (["it 1 residual = 1e-3", "it 2 residual = 2e-3", "it 3 residual = 3e-3", "it 4 residual = 4e-3"],
     ("warning", "worsening", "residual")),
])
def test_each_planted_problem_gets_the_right_severity(lines, expected):
    assert kinds(scan_lines(lines)) == [expected]


def test_a_configured_threshold_is_informational_by_default():
    config = MonitorConfig(thresholds={"max|T|": (400.0, "informational")})

    alerts = scan_lines(["step 100 max|T| = 399.0", "step 180 max|T| = 401.7"], config)

    assert kinds(alerts) == [("informational", "threshold", "max|T|")]
    assert alerts[0].line_number == 2


def test_clean_output_raises_nothing():
    assert scan_lines(STABLE_LOG) == []
    assert scan_lines([f"it {i} residual = {10.0 ** -i}" for i in range(1, 10)]) == []


def test_the_real_blow_up_warns_long_before_it_fails():
    alerts = scan_lines(UNSTABLE_LOG)

    first_warning = next(a for a in alerts if a.severity == "warning")
    first_critical = next(a for a in alerts if a.severity == "critical")
    assert "at step 200" in first_warning.message
    assert first_warning.line_number < first_critical.line_number
    assert {"overflow", "nan_inf", "error_line"} <= {a.check for a in alerts if a.severity == "critical"}


def test_each_check_fires_once_per_metric():
    alerts = scan_lines([f"step {i} max|T| = nan" for i in range(50)])

    assert kinds(alerts) == [("critical", "nan_inf", "max|T|")]


def test_names_that_only_start_like_residual_are_not_residuals():
    assert scan_lines([f"step {i} resolution = {i}.0" for i in range(1, 6)]) == []


def test_secrets_in_alert_lines_are_blanked():
    [alert] = scan_lines(["step 3 password = hunter2 residual = nan"])

    assert "hunter2" not in alert.line and "[redacted]" in alert.line


def test_stalled_output_is_reported_once_per_pause():
    monitor = LogMonitor(MonitorConfig(stall_seconds=60))

    assert monitor.stalled(30) == []
    assert kinds(monitor.stalled(61)) == [("informational", "stalled", None)]
    assert monitor.stalled(120) == []
    monitor.feed("step 1 residual = 1e-3", 1)
    assert len(monitor.stalled(61)) == 1


# --- Watcher ---------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_watcher_follows_a_growing_log(tmp_path):
    log, seen = tmp_path / "run.log", []
    log.write_text("\n".join(UNSTABLE_LOG[:3]) + "\n")
    watcher = LogWatcher(log, seen.extend)

    first = watcher.poll_once()
    with open(log, "a") as f:
        f.write("\n".join(UNSTABLE_LOG[3:]) + "\n")
    second = watcher.poll_once()

    assert {a.check for a in first} == {"sudden_jump"}
    assert "error_line" in {a.check for a in second}
    assert watcher.lines_read == len(UNSTABLE_LOG)
    assert seen == first + second
    assert watcher.poll_once() == []  # nothing is read twice


def test_an_unfinished_line_waits_until_output_pauses(tmp_path):
    log, clock = tmp_path / "run.log", FakeClock()
    log.write_text("step 1 max|T| = 400.0\nstep 2 max|T| = na")
    watcher = LogWatcher(log, lambda a: None, poll_seconds=1.0, clock=clock)

    assert watcher.poll_once() == [] and watcher.lines_read == 1
    with open(log, "a") as f:
        f.write("n")  # the line is now "... = nan", still without a newline
    watcher.poll_once()
    clock.now = 2.0
    assert kinds(watcher.poll_once()) == [("critical", "nan_inf", "max|T|")]


def test_a_missing_log_is_reported(tmp_path):
    clock = FakeClock()
    watcher = LogWatcher(tmp_path / "not_yet.log", lambda a: None, missing_after_seconds=30, clock=clock)

    assert watcher.poll_once() == []
    clock.now = 31
    assert kinds(watcher.poll_once()) == [("warning", "missing_output", None)]
    assert watcher.poll_once() == []


def test_a_replaced_log_is_read_from_the_start(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("\n".join(UNSTABLE_LOG) + "\n")
    watcher = LogWatcher(log, lambda a: None)
    watcher.poll_once()

    log.write_text("step 1 max|T| = nan\n")  # a new, shorter run
    alerts = watcher.poll_once()

    assert kinds(alerts) == [("critical", "nan_inf", "max|T|")]
    assert watcher.lines_read == 1


def test_the_simulation_keeps_running_after_a_critical_alert(tmp_path):
    """Watch a real solver run. The Critical alert must arrive while the solver is
    still running, and the solver must end on its own (exit code 1 from its own
    ERROR), not because SimGuard stopped it."""
    log = tmp_path / "run.log"
    solver = FIXTURES / "sample_project" / "heat2d.py"
    config = FIXTURES / "sample_project" / "config.json"
    running_at_first_critical = []

    with open(log, "w") as out:
        proc = subprocess.Popen([sys.executable, str(solver), str(config), "--delay", "0.15"], stdout=out)

        def on_alerts(alerts):
            if any(a.severity == "critical" for a in alerts) and not running_at_first_critical:
                running_at_first_critical.append(proc.poll() is None)

        watcher = LogWatcher(log, on_alerts, poll_seconds=0.05)
        watcher.start()
        try:
            exit_code = proc.wait(timeout=60)
            time.sleep(0.3)
        finally:
            watcher.stop()

    assert running_at_first_critical == [True]
    assert exit_code == 1
    assert "error_line" in {a.check for a in watcher.alerts}


# --- Backend alerts (session only, private to each researcher) -------------


@pytest.fixture
def backend(tmp_path):
    return TestClient(create_app(client_factory=lambda prefix: None, data_dir=tmp_path / "data"))


def headers(client, member):
    token = client.post("/sign-in", json={"member_id": member}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


ALERT = {"severity": "critical", "check": "nan_inf", "message": "max|T| became inf at step 2223",
         "metric": "max|T|", "line_number": 24, "line": "step  2223  max|T| = inf"}


def post(client, member, alerts=(ALERT,), project="heat-diffusion"):
    body = {"project_id": project, "run_label": "run.log", "alerts": list(alerts)}
    return client.post("/alerts", json=body, headers=headers(client, member))


def test_alerts_are_shown_to_their_researcher_newest_first(backend):
    post(backend, "researcher_a", [{**ALERT, "message": "first"}])
    post(backend, "researcher_a", [{**ALERT, "message": "second"}])

    alerts = backend.get("/alerts", headers=headers(backend, "researcher_a")).json()

    assert [a["message"] for a in alerts] == ["second", "first"]
    assert alerts[0]["project_id"] == "heat-diffusion" and alerts[0]["run_label"] == "run.log"


def test_researchers_only_see_their_own_alerts(backend):
    post(backend, "researcher_a")

    assert backend.get("/alerts", headers=headers(backend, "researcher_b")).json() == []


def test_alerts_need_sign_in_and_project_membership(backend):
    assert backend.post("/alerts", json={"project_id": "heat-diffusion", "run_label": "x", "alerts": []}).status_code == 401
    assert post(backend, "researcher_b", project="wave-propagation").status_code == 403


@pytest.mark.parametrize("bad", [{"severity": "panic"}, {"check": "vibes"}])
def test_invalid_alerts_are_refused(backend, bad):
    assert post(backend, "researcher_a", [{**ALERT, **bad}]).status_code == 422


def test_alerts_are_never_saved(tmp_path):
    data = tmp_path / "data"
    first = TestClient(create_app(client_factory=lambda prefix: None, data_dir=data))
    post(first, "researcher_a", [{**ALERT, "message": "UNIQUE-ALERT-MARKER"}])

    restarted = TestClient(create_app(client_factory=lambda prefix: None, data_dir=data))

    assert restarted.get("/alerts", headers=headers(restarted, "researcher_a")).json() == []
    for db_file in data.rglob("*.db"):
        assert b"UNIQUE-ALERT-MARKER" not in db_file.read_bytes()


def test_secrets_in_posted_alerts_are_blanked(backend):
    post(backend, "researcher_a", [{**ALERT, "line": "token = abc123secret residual = nan"}])

    [alert] = backend.get("/alerts", headers=headers(backend, "researcher_a")).json()
    assert "abc123secret" not in alert["line"]


# --- Connector service -----------------------------------------------------


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "heat_project"
    (root / ".git").mkdir(parents=True)
    for name in ("heat2d.py", "config.json", "run.log"):
        (root / name).write_text((FIXTURES / "sample_project" / name).read_text(), encoding="utf-8")
    (root / ".env").write_text("CREAO_HUB_KEY=capi_FAKE_decoy\n")
    (root / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    (root / ".git" / "config").write_text("[core]\n")
    (root / "results.bin").write_bytes(b"\x00\x01binary")
    return root


@pytest.fixture
def registry(tmp_path):
    return GrantRegistry(state_file=tmp_path / "grants.json")


def test_service_lists_only_files_that_may_be_selected(registry, project):
    service = TestClient(create_service(registry, http=object()))
    service.post("/grants", json={"root": str(project)})

    listed = service.post("/files", json={"root": str(project)}).json()

    assert [f["path"] for f in listed["files"]] == ["config.json", "heat2d.py", "run.log"]
    assert listed["truncated"] is False


def test_service_refuses_folders_that_were_not_granted(registry, project):
    service = TestClient(create_service(registry, http=object()))

    assert service.post("/files", json={"root": str(project)}).status_code == 403


def test_service_extracts_evidence_and_refuses_sensitive_files(registry, project):
    service = TestClient(create_service(registry, http=object()))
    service.post("/grants", json={"root": str(project)})

    ok = service.post("/evidence", json={"root": str(project), "selections": ["run.log", "heat2d.py:18-25"]})
    refused = service.post("/evidence", json={"root": str(project), "selections": [".env"]})

    assert ok.status_code == 200
    assert [e["path"] for e in ok.json()["evidence"]] == ["run.log", "heat2d.py"]
    assert refused.status_code == 403


def test_service_refuses_to_watch_sensitive_or_outside_files(registry, project, tmp_path):
    service = TestClient(create_service(registry, http=object()))
    service.post("/grants", json={"root": str(project)})
    body = {"root": str(project), "project_id": "heat-diffusion", "member_id": "researcher_a"}

    assert service.post("/watches", json={**body, "file": ".env"}).status_code == 403
    assert service.post("/watches", json={**body, "file": str(tmp_path / "elsewhere.log")}).status_code == 403


@pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")  # TestClient stands in for requests
def test_watched_alerts_reach_the_backend_for_that_researcher(registry, project, tmp_path):
    """Connector watcher -> backend -> the researcher's alert list, end to end."""
    backend = TestClient(create_app(client_factory=lambda prefix: None, data_dir=tmp_path / "data"))
    service = TestClient(create_service(registry, backend_url="http://testserver", http=backend,
                                        watcher_factory=lambda path, poster: LogWatcher(path, poster, poll_seconds=0.05)))
    service.post("/grants", json={"root": str(project)})
    (project / "live.log").write_text("")

    started = service.post("/watches", json={"root": str(project), "file": "live.log",
                                             "project_id": "heat-diffusion", "member_id": "researcher_a"}).json()
    (project / "live.log").write_text("step 1 max|T| = 400.0\nstep 2 max|T| = inf\n")
    alerts = []
    for _ in range(60):
        alerts = backend.get("/alerts", headers=headers(backend, "researcher_a")).json()
        if alerts:
            break
        time.sleep(0.05)
    stopped = service.delete(f"/watches/{started['id']}")

    assert [(a["severity"], a["check"], a["run_label"]) for a in alerts] == [("critical", "nan_inf", "live.log")]
    assert backend.get("/alerts", headers=headers(backend, "researcher_b")).json() == []
    assert stopped.json()["detail"].endswith("The simulation was not touched.")
    assert service.get("/watches").json() == []


def test_a_not_yet_created_log_can_be_watched(registry, project):
    service = TestClient(create_service(registry, http=object(),
                                        watcher_factory=lambda path, poster: LogWatcher(path, poster, poll_seconds=5)))
    service.post("/grants", json={"root": str(project)})

    started = service.post("/watches", json={"root": str(project), "file": "later.log",
                                             "project_id": "heat-diffusion", "member_id": "researcher_a"})

    assert started.status_code == 200
    service.delete(f"/watches/{started.json()['id']}")


def test_alerts_the_backend_did_not_receive_are_counted():
    class Down:
        def post(self, *args, **kwargs):
            raise ConnectionError("backend is down")

    poster = BackendPoster("http://127.0.0.1:9", "researcher_a", "heat-diffusion", "run.log", Down())
    poster(scan_lines(["step 1 max|T| = nan"]))

    assert poster.undelivered == 1
