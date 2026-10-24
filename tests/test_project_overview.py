"""Tests for the project overview ("What is this project?"): the project map built
on the researcher's computer, the backend checks, the private saved overview, and
its use in later diagnoses. CREAO is faked."""
import json
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import parse_run
from backend.db.database import Database
from backend.orchestration.overview import MAX_OVERVIEW_TOTAL_CHARS, OVERVIEW_INSTRUCTIONS
from backend.workspaces import demo
from local_connector.evidence.project_map import MAX_MAP_CHARS, MAX_MAP_EXCERPT_CHARS, build_project_map
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import create_service

SOLVER = '''"""Explicit 2D heat solver for the lab's diffusion study."""
import numpy as np


def initial_field(n):
    return np.zeros((n, n))


def solve(cfg):
    BODY_LINE_MARKER = 1  # inside a function: not part of the outline
    return BODY_LINE_MARKER


class Diagnostics:
    pass
'''
NOTEBOOK = {"cells": [
    {"cell_type": "markdown", "source": ["# Analysis of the heat runs\n", "Plots of max|T|."]},
    {"cell_type": "code", "source": ["import matplotlib.pyplot as plt\n", "plt.plot([1, 2])"]},
]}


@pytest.fixture
def research_project(tmp_path):
    root = tmp_path / "diffusion_study"
    files = {
        "README.md": "# Diffusion study\nSolves the 2D heat equation.\nAPI password = hunter2\n",
        "requirements.txt": "numpy\nmatplotlib\n",
        "config/params.yaml": "dt: 0.02\ndx: 0.01\nalpha: 0.001\n",
        "src/solver.py": SOLVER + "".join(f"\n\ndef helper_{i}():\n    pass\n" for i in range(3)),
        "notebooks/analysis.ipynb": json.dumps(NOTEBOOK),
        "tests/test_solver.py": "def test_solve():\n    assert True\n",
        ".env": "CREAO_HUB_KEY=capi_FAKE_decoy\n",
        "keys/id_rsa": "not really a key\n",
        ".git/config": "[core]\n",
        "venv/lib/site.py": "print('tool folder')\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "results.bin").write_bytes(b"\x00\x01binary")
    (root / "data").mkdir()
    for i in range(50):
        (root / "data" / f"run_{i:03d}.csv").write_text("t,maxT\n0,400\n")
    return root


@pytest.fixture
def registry(tmp_path):
    return GrantRegistry(state_file=tmp_path / "home" / "grants.json")


# --- The project map (built on the researcher's computer) ------------------


def test_the_map_covers_the_project_but_never_sensitive_files(registry, research_project):
    m = build_project_map(registry, registry.grant(research_project))

    assert "README.md" in m.tree and "solver.py" in m.tree and "data/  (50 files)" in m.tree
    assert "50 files: .csv x50" in m.tree  # big folders are summarized, not listed
    assert "run_000.csv" in m.tree and "run_049.csv" not in m.tree
    for never in (".env", "id_rsa", ".git", "venv", "results.bin", "site.py"):
        assert never not in m.tree
        assert all(never not in f for f in m.files)
    assert m.stats["left_out_by_rule"]["sensitive files"] >= 2  # .env and keys/id_rsa
    assert m.stats["left_out_by_rule"]["tool folders"] >= 1  # venv


def test_the_map_picks_key_files_and_skips_tests(registry, research_project):
    m = build_project_map(registry, registry.grant(research_project))

    kinds = {i.path: i.kind for i in m.items}
    assert kinds == {
        "README.md": "readme",
        "requirements.txt": "dependencies",
        "config/params.yaml": "config",
        "src/solver.py": "code outline",
        "notebooks/analysis.ipynb": "notebook outline",
    }


def test_code_outlines_show_signatures_not_bodies(registry, research_project):
    m = build_project_map(registry, registry.grant(research_project))

    outline = next(i for i in m.items if i.kind == "code outline").excerpt
    assert "Explicit 2D heat solver" in outline  # the opening lines
    assert "def helper_2" in outline and "|" in outline  # signatures, with line numbers
    assert "pass" not in outline.split("def helper_0", 1)[-1]  # function bodies further down are left out


def test_notebooks_are_outlined_by_their_headings(registry, research_project):
    m = build_project_map(registry, registry.grant(research_project))

    outline = next(i for i in m.items if i.kind == "notebook outline").excerpt
    assert "[markdown] # Analysis of the heat runs" in outline
    assert "(2 cells, 1 code cells)" in outline


def test_secrets_in_key_files_are_blanked(registry, research_project):
    m = build_project_map(registry, registry.grant(research_project))

    readme = next(i for i in m.items if i.kind == "readme").excerpt
    assert "hunter2" not in readme and "[redacted]" in readme
    assert m.redactions >= 1


def test_the_map_stays_within_its_size_limit(registry, tmp_path):
    root = tmp_path / "big_project"
    for i in range(12):
        path = root / "src" / f"model_{i}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(f"def f_{i}_{j}():\n    return {j}" for j in range(400)))
    (root / "README.md").write_text("x\n" * 5000)

    m = build_project_map(registry, registry.grant(root))

    assert m.total_chars <= MAX_MAP_CHARS
    assert all(len(i.excerpt) <= MAX_MAP_EXCERPT_CHARS for i in m.items)


def test_the_connector_service_serves_the_map_for_granted_folders_only(registry, research_project):
    service = TestClient(create_service(registry, http=object()))
    root = {"root": str(research_project)}

    assert service.post("/project-map", json=root).status_code == 403
    service.post("/grants", json=root)
    served = service.post("/project-map", json=root).json()

    assert served["project"] == "diffusion_study"
    assert "src/solver.py" in [e["path"] for e in served["excerpts"]]
    assert str(research_project) not in json.dumps(served)  # no local paths in what may be sent


# --- Overview through the backend ------------------------------------------

OVERVIEW = {
    "purpose": "Solve the 2D heat equation with an explicit scheme.",
    "structure": [{"path": "src/solver.py", "role": "Time stepping"}, {"path": "data", "role": "Run outputs"},
                  {"path": "src/imaginary.py", "role": "Something the agent made up"},
                  {"path": "diffusion_study/", "role": "The project folder itself"}],
    "how_to_run": "python src/solver.py config/params.yaml",
    "key_parameters": [{"name": "dt", "value": "0.02", "meaning": "time step"}],
    "open_questions": ["Which boundary condition is intended?"],
}
OVERVIEW_REPLY = "This project studies heat diffusion.\n```json\n" + json.dumps({"overview": OVERVIEW}) + "\n```"
DIAGNOSIS = parse_run(json.loads((Path(__file__).parent / "fixtures" / "creao_personal_agent_diagnosis.json")
                                 .read_text(encoding="utf-8"))["final_response"])


class FakeCreao:
    def __init__(self, reply=OVERVIEW_REPLY):
        self.reply = reply
        self.calls = []

    def run(self, inputs):
        self.calls.append(inputs)
        if inputs.get("output_format") == OVERVIEW_INSTRUCTIONS:
            return parse_run({"id": "run-overview-1", "status": "completed",
                              "result": {"chatText": self.reply, "text": "{}"}})
        return DIAGNOSIS


class Lab:
    def __init__(self, tmp_path, fake=None):
        self.fake = fake or FakeCreao()
        self.data = tmp_path / "data"
        self.client = TestClient(create_app(client_factory=lambda prefix: self.fake, data_dir=self.data))
        self.db = Database(self.data)
        self._headers = {}

    def headers(self, member="researcher_a"):
        if member not in self._headers:
            token = self.client.post("/sign-in", json={"member_id": member}).json()["token"]
            self._headers[member] = {"Authorization": f"Bearer {token}"}
        return self._headers[member]

    def send_overview(self, payload, member="researcher_a", project="heat-diffusion"):
        return self.client.post("/overview", json={"project_id": project, **payload}, headers=self.headers(member))

    def job(self, job_id, member="researcher_a"):
        return self.client.get(f"/jobs/{job_id}", headers=self.headers(member)).json()

    def saved(self, member="researcher_a", project="heat-diffusion"):
        return self.client.get(f"/projects/{project}/overview", headers=self.headers(member))


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


@pytest.fixture
def payload(registry, research_project):
    return build_project_map(registry, registry.grant(research_project)).to_payload()


def test_an_overview_is_made_and_saved_privately(lab, payload):
    started = lab.send_overview(payload)
    job = lab.job(started.json()["job_id"])
    saved = lab.saved().json()

    assert started.status_code == 202
    assert job["status"] == "completed" and job["kind"] == "overview"
    assert saved["overview"]["purpose"] == OVERVIEW["purpose"]
    assert saved["overview"]["key_parameters"][0]["name"] == "dt"
    assert saved["creao_run_id"] == "run-overview-1"
    assert "```json" not in saved["reply"]


def test_paths_the_agent_invented_are_marked(lab, payload):
    lab.send_overview(payload)

    flags = {s["path"]: s["in_project"] for s in lab.saved().json()["overview"]["structure"]}
    assert flags == {"src/solver.py": True, "data": True, "src/imaginary.py": False, "diffusion_study/": True}


def test_creao_gets_the_map_and_the_overview_request(lab, payload):
    lab.send_overview(payload)

    inputs = lab.fake.calls[0]
    assert inputs["output_format"] == OVERVIEW_INSTRUCTIONS
    assert inputs["focus_areas"] == "all"
    assert "No problem to diagnose yet" in inputs["observed_symptom"]
    assert "Folder tree:" in inputs["observed_symptom"] and "--- src/solver.py (code outline" in inputs["observed_symptom"]
    assert "hunter2" not in json.dumps(inputs)


def test_the_overview_is_private(lab, payload):
    lab.send_overview(payload)

    assert lab.saved(member="researcher_b").status_code == 404  # B has no overview, and never sees A's
    assert OVERVIEW["purpose"].encode() not in lab.db.shared_path.read_bytes()


def test_later_diagnoses_include_the_saved_overview(lab, payload):
    before = lab.client.post("/diagnose", headers=lab.headers(), json={"project_id": "heat-diffusion", "symptom": "x"})
    lab.send_overview(payload)
    after = lab.client.post("/diagnose", headers=lab.headers(), json={"project_id": "heat-diffusion", "symptom": "x"})

    assert before.json()["used_project_overview"] is False
    assert after.json()["used_project_overview"] is True
    sent = lab.fake.calls[-1]["observed_symptom"]
    assert "Project context (from the researcher's saved overview" in sent and OVERVIEW["purpose"] in sent


def test_an_answer_without_the_overview_block_keeps_the_prose(tmp_path, payload):
    lab = Lab(tmp_path, FakeCreao(reply="It is a heat solver; see solver.py."))

    lab.send_overview(payload)
    saved = lab.saved().json()

    assert saved["overview"] is None
    assert saved["reply"] == "It is a heat solver; see solver.py."


@pytest.mark.parametrize("change, status", [
    ({"tree": "proj/  (2 files)\n  .env  (20 B)\n  main.py  (1 KB)"}, 422),
    ({"excerpts": [{"path": "keys/id_rsa", "kind": "config", "excerpt": "x", "truncated": False}]}, 422),
    ({"files": ["C:/Users/me/project/main.py"]}, 422),
    ({"tree": "x" * 9000}, 413),
    ({"excerpts": [{"path": f"f{i}.py", "kind": "code outline", "excerpt": "x" * 2400, "truncated": True}
                   for i in range(12)]}, 413),
])
def test_unsafe_or_oversized_maps_are_refused(lab, payload, change, status):
    response = lab.send_overview({**payload, **change})

    assert response.status_code == status
    assert lab.fake.calls == []


def test_overviews_need_membership(lab, payload):
    assert lab.send_overview(payload, member="researcher_b", project="wave-propagation").status_code == 403
    assert lab.client.post("/overview", json={"project_id": "heat-diffusion", **payload}).status_code == 401


def test_the_overview_limit_fits_what_the_connector_sends():
    assert MAX_MAP_CHARS < MAX_OVERVIEW_TOTAL_CHARS


# --- Adding a real project -------------------------------------------------


def test_the_leader_can_add_a_project(lab):
    created = lab.client.post("/projects", headers=lab.headers("leader"),
                              json={"id": "diffusion-study", "name": "Diffusion study", "member_ids": ["researcher_a"]})

    assert created.status_code == 201
    assert created.json()["members"] == ["leader", "researcher_a"]
    projects = lab.client.get("/me", headers=lab.headers("researcher_a")).json()["projects"]
    assert "diffusion-study" in [p["id"] for p in projects]
    assert "diffusion-study" not in [p["id"] for p in lab.client.get("/me", headers=lab.headers("researcher_b")).json()["projects"]]


@pytest.mark.parametrize("body, member, status", [
    ({"id": "x-project", "name": "X"}, "researcher_a", 403),
    ({"id": "Bad ID!", "name": "X"}, "leader", 422),
    ({"id": "heat-diffusion", "name": "Duplicate"}, "leader", 422),
    ({"id": "x-project", "name": "X", "member_ids": ["nobody"]}, "leader", 422),
])
def test_bad_projects_are_refused(lab, body, member, status):
    assert lab.client.post("/projects", headers=lab.headers(member), json=body).status_code == status


def test_the_demo_command_can_add_a_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EXPANDRG_DATA_DIR", str(tmp_path / "data"))

    assert demo.main(["--add-project", "my-research", "--name", "My research", "--members", "researcher_a"]) == 0

    assert "Added project my-research" in capsys.readouterr().out
    with closing(Database(tmp_path / "data").shared()) as conn:
        members = {r[0] for r in conn.execute("SELECT member_id FROM project_members WHERE project_id = 'my-research'")}
    assert members == {"leader", "researcher_a"}
