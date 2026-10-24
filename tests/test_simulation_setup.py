"""Project setup, explicit builds/runs, automatic monitoring, and retained versions."""
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.orchestration.simulation_setup import InvalidSetup, clean_plan
from local_connector.experiments.manifest import Limits
from local_connector.experiments.sandbox import Outcome, run_limited
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import create_service
from local_connector.simulations import SimulationManager


def python_plan():
    return {"title": "Heat example", "language": "Python", "summary": "Run the project's example input.",
            "working_directory": ".", "build_commands": [], "run_command": ["{python}", "main.py", "input.json"],
            "source_files": ["README.md", "main.py", "input.json"], "environment_notes": [], "questions": []}


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def lab(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    (root / "README.md").write_text("Run python main.py input.json. Python standard library only.")
    (root / "input.json").write_text('{"steps":3}')
    (root / "main.py").write_text(
        "import json, sys, time\n"
        "steps = json.load(open(sys.argv[1]))['steps']\n"
        "for step in range(1, steps+1):\n"
        "    print(f'step {step} energy = {1.0/step}', flush=True)\n"
        "    time.sleep(.05)\n")
    (root / ".env").write_text("CREAO_HUB_KEY=capi_do_not_copy_this_key")
    calls, slots, executions = [], [], []
    fake = SimpleNamespace(plan=python_plan())
    def factory(prefix):
        slots.append(prefix)
        def run(inputs):
            calls.append(inputs)
            return SimpleNamespace(run_id="setup-provider", reply="```json\n" + json.dumps({"simulation_setup": fake.plan}) + "\n```")
        return SimpleNamespace(run=run)
    backend = TestClient(create_app(client_factory=factory, data_dir=tmp_path / "data"))
    headers = {}
    for person in ("researcher_a", "researcher_b", "leader"):
        token = backend.post("/sign-in", json={"member_id": person}).json()["token"]
        headers[person] = {"Authorization": "Bearer " + token}
    class Bridge:
        def get(self, url, **kwargs):
            return backend.get(url.removeprefix("http://backend"), headers=kwargs.get("headers"))
        def post(self, url, **kwargs):
            return backend.post(url.removeprefix("http://backend"), json=kwargs.get("json"), headers=kwargs.get("headers"))
    registry = GrantRegistry(tmp_path / "grants.json")
    registry.grant(root)
    def runner(*args, **kwargs):
        executions.append(args[0])
        return run_limited(*args, **kwargs)
    manager = SimulationManager(tmp_path / "versions", runner=runner)
    local = TestClient(create_service(registry=registry, backend_url="http://backend", http=Bridge(), simulations=manager))
    return SimpleNamespace(root=root, manager=manager, local=local, backend=backend, headers=headers,
                           calls=calls, slots=slots, executions=executions, fake=fake)


def inspect(lab):
    response = lab.local.post("/simulations/inspect", json={"root": str(lab.root), "project_id": "heat-diffusion"}, headers=lab.headers["researcher_a"])
    assert response.status_code == 201, response.text
    return response.json()


def detect(lab, prepared):
    response = lab.backend.post("/simulation-setup", json={"project_id": "heat-diffusion", **prepared["context"]}, headers=lab.headers["researcher_a"])
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = lab.backend.get(f"/jobs/{job_id}", headers=lab.headers["researcher_a"]).json()
    assert job["status"] == "completed", job
    return job["diagnosis"]["plan"]


def ready(lab):
    prepared = inspect(lab)
    plan = detect(lab, prepared)
    identifier = prepared["simulation"]["id"]
    response = lab.local.post(f"/simulations/{identifier}/plan", json={"plan": plan}, headers=lab.headers["researcher_a"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    return identifier


def test_setup_executes_nothing_and_uses_the_owners_existing_creao_agent(lab):
    before = hashes(lab.root)
    identifier = ready(lab)
    assert lab.executions == []
    assert lab.slots == ["AGENT_1"]
    assert lab.calls[0]["analysis_depth"] == "standard"
    assert "chatModelId" not in lab.calls[0]
    assert "main.py" in lab.calls[0]["observed_symptom"]
    assert "capi_do_not_copy_this_key" not in json.dumps(lab.calls)
    assert not (lab.manager.home / identifier / "snapshot" / ".env").exists()
    assert hashes(lab.root) == before


def test_real_python_execution_automatically_connects_metrics_and_keeps_the_snapshot(lab):
    before = hashes(lab.root)
    identifier = ready(lab)
    started = lab.local.post(f"/simulations/{identifier}/run", headers=lab.headers["researcher_a"])
    assert started.status_code == 200, started.text
    run_id = started.json()["backend_run_id"]
    assert run_id
    lab.manager.wait(20)
    detail = lab.local.get(f"/simulations/{identifier}", headers=lab.headers["researcher_a"]).json()
    assert detail["status"] == "finished", detail
    assert "step 3 energy" in detail["output"]
    assert len(lab.executions) == 1
    observed = lab.backend.get(f"/simulation-runs/{run_id}", headers=lab.headers["researcher_a"]).json()
    assert observed["series"]["energy"] == [[1, 1.0], [2, .5], [3, 1/3]]
    assert observed["watching"] is False
    assert "Version 1" in observed["run_label"]
    assert lab.backend.get(f"/simulation-runs/{run_id}", headers=lab.headers["leader"]).status_code == 200
    assert lab.backend.get(f"/simulation-runs/{run_id}", headers=lab.headers["researcher_b"]).status_code == 404
    assert hashes(lab.root) == before
    assert (lab.manager.home / identifier / "snapshot" / "input.json").read_text() == '{"steps":3}'


def test_new_versions_and_repeats_do_not_overwrite_old_code_inputs_or_results(lab):
    identifier = ready(lab)
    first = lab.manager.get(identifier)
    (lab.root / "main.py").write_text("print('changed by researcher')")
    (lab.root / "input.json").write_text('{"steps":20}')
    second = inspect(lab)["simulation"]
    assert second["version"] == 2 and second["fingerprint"] != first["fingerprint"]
    repeated = lab.local.post(f"/simulations/{identifier}/repeat", headers=lab.headers["researcher_a"])
    assert repeated.status_code == 201
    third = repeated.json()
    assert third["version"] == 3 and third["source_version"] == 1
    assert third["fingerprint"] == first["fingerprint"]
    assert third["status"] == "ready" and lab.executions == []
    assert (lab.manager.home / third["id"] / "snapshot" / "input.json").read_text() == '{"steps":3}'
    reopened = SimulationManager(lab.manager.home)
    assert len(reopened.list("researcher_a", "heat-diffusion")) == 3


def test_setup_and_execution_enforce_identity_project_access_and_completed_setup(lab):
    body = {"root": str(lab.root), "project_id": "heat-diffusion"}
    assert lab.local.post("/simulations/inspect", json=body).status_code == 401
    assert lab.local.post("/simulations/inspect", json=body, headers=lab.headers["leader"]).status_code == 403
    assert lab.local.post("/simulations/inspect", json={**body, "project_id": "wave-propagation"}, headers=lab.headers["researcher_b"]).status_code == 403
    identifier = inspect(lab)["simulation"]["id"]
    for suffix in ("", "/context"):
        assert lab.local.get(f"/simulations/{identifier}{suffix}", headers=lab.headers["researcher_b"]).status_code == 404
    for action in ("run", "stop", "repeat"):
        assert lab.local.post(f"/simulations/{identifier}/{action}", headers=lab.headers["researcher_b"]).status_code == 404
    assert lab.local.post(f"/simulations/{identifier}/run", headers=lab.headers["researcher_a"]).status_code == 422
    assert lab.executions == []


@pytest.mark.parametrize("change", [
    {"run_command": "python main.py"}, {"run_command": ["cmd", "/c", "anything"]},
    {"run_command": ["{python}", "main.py", "&&", "other"]}, {"working_directory": "../outside"},
    {"source_files": ["imaginary.py"]}, {"run_command": ["/host/program"]},
])
def test_unsafe_or_ungrounded_plans_are_not_runnable(change):
    with pytest.raises(InvalidSetup):
        clean_plan({**python_plan(), **change}, ["README.md", "main.py", "input.json"])


def test_windows_compiler_and_executable_names_are_supported():
    plan = {**python_plan(), "source_files": ["solver.f90"],
            "build_commands": [["gfortran.exe", "solver.f90", "-o", "solver.exe"]],
            "run_command": ["solver.exe"]}
    checked = clean_plan(plan, ["solver.f90"])
    assert checked["build_commands"][0][0] == "gfortran"
    assert checked["run_command"] == ["./solver.exe"]


class Monitor:
    def __init__(self):
        self.lines, self.ended = [], []
    def feed(self, lines):
        self.lines.extend(lines)
    def end(self, code):
        self.ended.append(code)


def test_fortran_build_failure_never_launches_the_executable(tmp_path):
    root = tmp_path / "fortran"
    root.mkdir()
    (root / "solver.f90").write_text("program solver\nend program")
    calls = []
    def compiler(command, cwd, log, limits, cancel, **kwargs):
        calls.append(command)
        log.write_text("error: compilation failed\n")
        return Outcome("failed", "Compilation failed", 1, .1)
    manager = SimulationManager(tmp_path / "versions", runner=compiler, find_program=lambda p: "compiler")
    registry = GrantRegistry(tmp_path / "grants.json")
    record = manager.prepare(registry.grant(root), "researcher_a", "heat-diffusion")
    plan = {**python_plan(), "language": "Fortran", "source_files": ["solver.f90"],
            "build_commands": [["gfortran", "solver.f90", "-o", "solver"]], "run_command": ["./solver"]}
    manager.set_plan(record["id"], plan)
    monitor = Monitor()
    manager.run(record["id"], lambda r: (monitor, "tracked-run"))
    manager.wait(10)
    assert [c[0] for c in calls] == ["compiler"]
    assert manager.get(record["id"])["status"] == "failed"
    assert "Build step 1" in manager.get(record["id"])["reason"]
    assert "compilation failed" in "\n".join(monitor.lines)
    assert monitor.ended == [1]


def test_missing_compiler_is_reported_before_a_run_is_registered(tmp_path):
    root = tmp_path / "fortran"
    root.mkdir()
    (root / "solver.f90").write_text("program solver\nend program")
    registry = GrantRegistry(tmp_path / "grants.json")
    manager = SimulationManager(tmp_path / "versions", find_program=lambda p: None)
    record = manager.prepare(registry.grant(root), "a", "p")
    plan = {**python_plan(), "source_files": ["solver.f90"], "build_commands": [["gfortran", "solver.f90", "-o", "solver"]], "run_command": ["./solver"]}
    saved = manager.set_plan(record["id"], plan)
    assert saved["missing_tools"] == ["gfortran"] and saved["status"] == "needs_input"
    with pytest.raises(InvalidSetup):
        manager.run(record["id"], lambda r: pytest.fail("An incomplete setup must not start a monitored run"))


def test_repeated_run_clicks_do_not_start_twice_and_stop_is_explicit(lab):
    identifier = ready(lab)
    entered = threading.Event()
    def slow(command, cwd, log, limits, cancel, **kwargs):
        entered.set()
        log.write_text("step 1 energy = 1\n")
        cancel.wait(5)
        return Outcome("stopped", "Stopped by you", None, .1)
    lab.manager.runner = slow
    monitors = []
    def factory(record):
        monitor = Monitor(); monitors.append(monitor); return monitor, "backend-run"
    lab.manager.run(identifier, factory)
    assert entered.wait(5)
    lab.manager.run(identifier, factory)
    assert len(monitors) == 1
    lab.manager.stop(identifier)
    lab.manager.wait(10)
    assert lab.manager.get(identifier)["status"] == "stopped"


def test_ambiguous_agent_setup_stays_blocked_and_can_be_resumed(lab):
    prepared = inspect(lab)
    identifier = prepared["simulation"]["id"]
    lab.fake.plan = {**python_plan(), "run_command": [], "questions": ["Which input should be used?"]}
    plan = detect(lab, prepared)
    saved = lab.local.post(f"/simulations/{identifier}/plan", json={"plan": plan}, headers=lab.headers["researcher_a"]).json()
    assert saved["status"] == "needs_input"
    assert lab.local.post(f"/simulations/{identifier}/run", headers=lab.headers["researcher_a"]).status_code == 422
    response = lab.local.post(f"/simulations/{identifier}/planning", json={"job_id": "saved-job"}, headers=lab.headers["researcher_a"])
    assert response.json()["setup_job_id"] == "saved-job"
    reopened = SimulationManager(lab.manager.home)
    assert reopened.get(identifier)["setup_job_id"] == "saved-job"
    assert lab.executions == []


def test_invalid_agent_response_is_an_actionable_setup_error(lab):
    prepared = inspect(lab)
    lab.fake.plan = {"run_command": ["bash", "-c", "something"]}
    response = lab.backend.post("/simulation-setup", json={"project_id": "heat-diffusion", **prepared["context"]}, headers=lab.headers["researcher_a"])
    job = lab.backend.get("/jobs/" + response.json()["job_id"], headers=lab.headers["researcher_a"]).json()
    assert job["status"] == "failed" and job["error"]["type"] == "setup"
    assert "supported build tool" in job["error"]["message"]


def test_existing_project_venv_is_reused_without_copying_or_recreating_it(lab):
    environment = lab.root / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True, capture_output=True)
    executable = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    location = subprocess.check_output([str(executable), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
    package = Path(location) / "expandrg_existing_dependency.py"
    package.write_text("VALUE = 42\n")
    (lab.root / "main.py").write_text("import expandrg_existing_dependency as dep\nprint(f'step 1 energy = {dep.VALUE}', flush=True)\n")
    before = hashes(lab.root)
    identifier = ready(lab)
    record = lab.manager.get(identifier)
    assert Path(record["runtime"]["python"]) == executable
    assert not (lab.manager.home / identifier / "snapshot" / ".venv").exists()
    response = lab.local.post(f"/simulations/{identifier}/run", headers=lab.headers["researcher_a"])
    assert response.status_code == 200
    lab.manager.wait(20)
    detail = lab.manager.detail(identifier)
    assert detail["status"] == "finished", detail
    assert "step 1 energy = 42" in detail["output"]
    assert Path(lab.executions[-1][0]) == executable
    assert not (lab.manager.home / identifier / "work" / ".venv").exists()
    assert hashes(lab.root) == before
    repeated = lab.manager.repeat(identifier)
    assert repeated["runtime"]["python"] == record["runtime"]["python"]
    assert repeated["runtime"]["source_root"] == str(lab.root.resolve())


def test_installed_compiler_can_be_connected_without_putting_it_on_expandrg_path(lab, tmp_path):
    identifier = ready(lab)
    executable = tmp_path / "outside-path" / "gfortran.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"compiler test stub")
    response = lab.local.post(f"/simulations/{identifier}/environment", json={"compiler": str(executable)}, headers=lab.headers["researcher_a"])
    assert response.status_code == 200
    assert response.json()["runtime"]["tools"]["{fortran}"] == str(executable)
    assert lab.executions == []
    # The model receives an available alias, never this machine's actual path.
    context = lab.local.get(f"/simulations/{identifier}/context", headers=lab.headers["researcher_a"]).json()
    assert context["machine"]["preferred_compilers"]["Fortran"] == "{fortran}"
    assert str(executable) not in json.dumps(context["machine"])
    assert lab.local.post(f"/simulations/{identifier}/environment", json={"compiler": str(executable)}, headers=lab.headers["researcher_b"]).status_code == 404
