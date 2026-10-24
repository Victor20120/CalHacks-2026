"""Tests for experiments: the run manifest, experiment copies, the limited process
sandbox, Run / Run all / stop, and the comparison with the original run.
Real processes run locally (small Python scripts and the sample heat solver).
No network and no CREAO."""
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_connector.experiments.manager import ExperimentManager
from local_connector.experiments.manifest import Limits, ManifestError, load_manifest
from local_connector.experiments.sandbox import clean_environment, run_limited
from local_connector.experiments.workspace import ExperimentRefused
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import create_service

SAMPLE = Path(__file__).parent / "fixtures" / "sample_project"


def hashes(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def project(tmp_path):
    """The sample heat solver with a secret, a .git folder and a virtual environment next to it."""
    root = tmp_path / "heat"
    root.mkdir()
    for name in ("heat2d.py", "config.json", "run.log", "expandrg.json"):
        (root / name).write_bytes((SAMPLE / name).read_bytes())
    (root / ".env").write_text("CREAO_HUB_KEY=capi_FAKE_never_copied\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (root / "venv").mkdir()
    (root / "venv" / "big.txt").write_text("tool folder")
    return root


@pytest.fixture
def lab(tmp_path):
    return ExperimentManager(home=tmp_path / "experiments")


def prepare(lab, project, changes=None, **options):
    return lab.prepare(project, "heat", "researcher_a", "heat-diffusion", "heat2d", changes or {}, **options)


# --- The run manifest --------------------------------------------------------


def test_the_sample_manifest_is_read(project):
    [entry] = load_manifest(project).values()
    assert entry.name == "heat2d" and entry.config == "config.json"
    assert "dt" in entry.parameters and entry.limits == Limits(300, 1024, 20)


@pytest.mark.parametrize("manifest, problem", [
    (None, "no expandrg.json"),
    ("{not json", "not valid JSON"),
    ({"entry_points": []}, "entry_points"),
    ({"entry_points": [{"name": "a b", "command": ["x"]}]}, "short name"),
    ({"entry_points": [{"name": "a", "command": "python run.py"}]}, "list of words"),
    ({"entry_points": [{"name": "a", "command": ["x"], "config": "../outside.json", "parameters": ["dt"]}]}, "inside the project"),
    ({"entry_points": [{"name": "a", "command": ["x"], "parameters": ["dt"]}]}, "need a \"config\""),
    ({"entry_points": [{"name": "a", "command": ["x"], "limits": {"wall_seconds": -1}}]}, "positive number"),
])
def test_bad_manifests_are_explained(tmp_path, manifest, problem):
    if manifest is not None:
        (tmp_path / "expandrg.json").write_text(manifest if isinstance(manifest, str) else json.dumps(manifest))
    with pytest.raises(ManifestError, match=problem):
        load_manifest(tmp_path)


def test_limits_are_capped(tmp_path):
    (tmp_path / "expandrg.json").write_text(json.dumps(
        {"entry_points": [{"name": "a", "command": ["x"], "limits": {"wall_seconds": 10**9}}]}))
    assert load_manifest(tmp_path)["a"].limits.wall_seconds == 6 * 3600


# --- Preparing: a copy, never a run --------------------------------------------


def test_preparing_copies_the_project_and_changes_only_the_copy(lab, project):
    before = hashes(project)
    record = prepare(lab, project, {"dt": 0.02})

    copy = lab.home / record["id"] / "project"
    assert json.loads((copy / "config.json").read_text())["dt"] == 0.02
    assert json.loads((project / "config.json").read_text())["dt"] == 0.03
    assert hashes(project) == before  # the project is byte-for-byte unchanged
    assert not (copy / ".env").exists() and not (copy / ".git").exists() and not (copy / "venv").exists()
    assert record["changes"] == {"dt": [0.03, 0.02]} and record["label"] == "dt = 0.02"


def test_preparing_never_runs_anything(lab, project):
    record = prepare(lab, project, {"dt": 0.02})
    time.sleep(0.3)

    assert lab.detail(record["id"])["status"] == "prepared"
    assert not (lab.home / record["id"] / "output.log").exists()


@pytest.mark.parametrize("changes, problem", [
    ({"initial_peak": 1.0}, "not a parameter"),
    ({"dt": "fast"}, "must be a float"),
])
def test_changes_the_manifest_does_not_allow_are_refused(lab, project, changes, problem):
    with pytest.raises(ExperimentRefused, match=problem):
        prepare(lab, project, changes)
    assert not any(lab.home.iterdir())  # nothing left behind


def test_an_unknown_entry_point_is_refused(lab, project):
    with pytest.raises(ExperimentRefused, match="no entry point"):
        lab.prepare(project, "heat", "researcher_a", "heat-diffusion", "rm-everything", {})


# --- The sandbox ---------------------------------------------------------------


def run_script(tmp_path, code, limits, cancel_after=None):
    script = tmp_path / "script.py"
    script.write_text(code)
    cancel = threading.Event()
    if cancel_after:
        threading.Timer(cancel_after, cancel.set).start()
    outcome = run_limited([sys.executable, str(script)], tmp_path, tmp_path / "out.log", limits, cancel)
    return outcome, (tmp_path / "out.log").read_text(errors="replace")


def test_a_runaway_run_is_stopped_at_its_time_limit(tmp_path):
    outcome, _ = run_script(tmp_path, "import time; time.sleep(30)", Limits(1, 512, 5))
    assert outcome.status == "stopped" and "time limit" in outcome.reason and outcome.seconds < 10


def test_endless_output_is_stopped_at_its_output_limit(tmp_path):
    outcome, _ = run_script(tmp_path, "while True: print('x' * 1000)", Limits(30, 512, 1))
    assert outcome.status == "stopped" and "output limit" in outcome.reason


def test_a_run_needing_too_much_memory_is_stopped(tmp_path):
    outcome, _ = run_script(tmp_path, "x = bytearray(1500 * 1024 * 1024)", Limits(30, 300, 5))
    assert outcome.status == "stopped" and "memory limit" in outcome.reason


def test_the_stop_button_ends_the_run_and_what_it_started(tmp_path):
    marker = tmp_path / "child_still_alive"
    code = ("import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3); open(\"{marker.as_posix()}\", \"w\")'])\n"
            "time.sleep(30)\n")
    outcome, _ = run_script(tmp_path, code, Limits(30, 512, 5), cancel_after=1.0)
    time.sleep(3)

    assert outcome.status == "stopped" and outcome.reason == "Stopped by you."
    assert not marker.exists()  # the child the run started was stopped too


def test_runs_never_see_keys_or_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("CREAO_HUB_KEY", "capi_FAKE_secret")
    monkeypatch.setenv("SOME_API_TOKEN", "t0ken")
    outcome, out = run_script(tmp_path, "import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))",
                              Limits(30, 512, 5))
    assert outcome.status == "finished" and out.strip() == "[]"
    assert "PATH" in {k.upper() for k in clean_environment()}


# --- Running and comparing -----------------------------------------------------


def test_run_compares_a_stable_time_step_with_the_blow_up(lab, project):
    before = hashes(project)
    record = prepare(lab, project, {"dt": 0.02, "steps": 400}, baseline="run.log")
    lab.run([record["id"]])
    lab.wait(60)

    done = lab.detail(record["id"])
    assert done["status"] == "finished" and done["exit_code"] == 0
    assert done["last_step"] == 400 and not [a for a in done["alerts"] if a["severity"] == "critical"]
    assert all(v is not None and v < 400 for _, v in done["series"]["max|T|"])  # decays instead of blowing up
    assert hashes(project) == before  # running didn't touch the project either


def test_run_all_runs_the_set_one_at_a_time_and_stop_cancels_the_rest(tmp_path):
    root = tmp_path / "slow"
    root.mkdir()
    (root / "slow.py").write_text("import time\nfor i in range(100):\n    print(f'step {i} x = {i}', flush=True)\n    time.sleep(0.1)\n")
    (root / "expandrg.json").write_text(json.dumps({"entry_points": [{"name": "slow", "command": ["{python}", "slow.py"]}]}))
    lab = ExperimentManager(home=tmp_path / "experiments")
    first, second = (lab.prepare(root, "slow", "researcher_a", "p", "slow", {}, set_id="s1") for _ in range(2))

    lab.run([first["id"], second["id"]])
    time.sleep(1.0)
    states = (lab.detail(first["id"])["status"], lab.detail(second["id"])["status"])
    lab.stop(second["id"])  # still waiting: never starts
    lab.stop(first["id"])
    lab.wait(30)

    assert states == ("running", "queued")
    assert lab.detail(second["id"])["reason"] == "Stopped by you before it started."
    assert lab.detail(first["id"])["status"] == "stopped"
    assert not (lab.home / second["id"] / "output.log").exists()


def test_a_restarted_connector_does_not_leave_experiments_running(lab, project):
    record = prepare(lab, project)
    path = lab.home / record["id"] / "experiment.json"
    path.write_text(json.dumps({**record, "status": "running"}))

    reopened = ExperimentManager(home=lab.home)

    assert reopened.detail(record["id"])["status"] == "stopped"


# --- The connector service -------------------------------------------------------


@pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")
def test_the_service_prepares_runs_and_compares(project, tmp_path):
    registry = GrantRegistry(state_file=tmp_path / "grants.json")
    lab = ExperimentManager(home=tmp_path / "experiments")
    service = TestClient(create_service(registry, http=object(), experiments=lab))
    service.post("/grants", json={"root": str(project)})

    manifest = service.post("/manifest", json={"root": str(project)}).json()
    body = {"root": str(project), "member_id": "researcher_a", "project_id": "heat-diffusion", "entry": "heat2d",
            "changes": {"dt": 0.02, "steps": 300}, "baseline": "run.log"}
    prepared = service.post("/experiments", json=body)
    refused = service.post("/experiments", json={**body, "changes": {"initial_peak": 1}})
    outside = service.post("/experiments", json={**body, "baseline": str(tmp_path / "elsewhere.log")})
    not_granted = service.post("/manifest", json={"root": str(tmp_path)})
    service.post("/experiments/run", json={"ids": [prepared.json()["id"]]})
    lab.wait(60)
    detail = service.get(f"/experiments/{prepared.json()['id']}").json()

    assert manifest["entry_points"][0]["values"]["dt"] == 0.03 and "run.log" in manifest["logs"]
    assert prepared.status_code == 201 and prepared.json()["status"] == "prepared"
    assert refused.status_code == 422 and outside.status_code == 403 and not_granted.status_code == 403
    assert detail["status"] == "finished" and detail["last_step"] == 300
    assert detail["baseline_result"]["last_step"] == 2223  # the original blew up there
    assert [e["id"] for e in service.get("/experiments", params={"member_id": "researcher_a"}).json()] == [detail["id"]]
    assert service.delete(f"/experiments/{detail['id']}").json() == {"status": "deleted"}
    assert service.get(f"/experiments/{detail['id']}").status_code == 404
