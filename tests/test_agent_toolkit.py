"""A personal agent's toolkit: read-only files, Super Brain search and runs, with the researcher's permissions.

CREAO can't call ExpandRG's tools, so the agent asks for them in its reply and ExpandRG
runs them. CREAO is faked; the local connector is the real one, on a temporary folder.
"""
import json
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import ChatSettings, parse_run
from backend.db.database import Database
from backend.orchestration.toolkit import MAX_ROUND_CHARS, MAX_TOOL_REQUESTS, ConnectorClient
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import create_service

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "sample_project"
DIAGNOSIS = parse_run(json.loads((FIXTURES / "creao_personal_agent_diagnosis.json").read_text(encoding="utf-8"))["final_response"])
FINDING_TITLE = "Explicit time step above the CFL limit makes the heat solver blow up"


@pytest.fixture
def project(tmp_path):
    """The sample heat project, plus files an agent must never get (made here, never committed)."""
    root = tmp_path / "heat_project"
    root.mkdir()
    for name in ("heat2d.py", "config.json", "run.log"):
        (root / name).write_text((SAMPLE / name).read_text(encoding="utf-8"), encoding="utf-8")
    (root / "solver.f90").write_text("".join(f"  x({i}) = x({i}) + dt * rhs({i})  ! update of cell {i}\n" for i in range(1, 1001)))
    (root / "params.py").write_text('API_KEY = "sk_live_decoy_value_12345"\ndt = 0.03\n')
    (root / "trap.py").write_text("open('pwned.txt', 'w').write('executed')\n")
    (root / ".env").write_text("CREAO_HUB_KEY=capi_decoy_secret_value_123\n")
    (root / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[remote]\nurl = https://token@github.com/x\n")
    (root / "id_rsa").write_text("not really a key\n")
    (root / "results.bin").write_bytes(b"\x00\x01\x02binary")
    (root / "notes.xyz").write_text("plain text, but not a research file type\n")
    (tmp_path / "outside.py").write_text("print('outside the granted folder')\n")
    return root


@pytest.fixture
def connector(tmp_path, project):
    registry = GrantRegistry(state_file=tmp_path / "grants.json")
    registry.grant(project)
    return SimpleNamespace(client=TestClient(create_service(registry=registry, http=SimpleNamespace())), registry=registry)


class Local:
    """The connector as the backend reaches it (the test client takes no timeout)."""

    def __init__(self, client):
        self.client = client

    def post(self, url, json, timeout):
        return self.client.post(url, json=json)


def read(connector, path, lines=None, folder="heat_project"):
    return connector.client.post("/tools/read_project_file", json={"folder": folder, "path": path, "lines": lines})


# --- Files, through the local connector ------------------------------------------------------


def test_lists_only_research_files_a_researcher_would_share(connector):
    listed = connector.client.post("/tools/list_project_files", json={"folder": "heat_project"}).json()
    paths = {f["path"] for f in listed["files"]}
    assert {"heat2d.py", "config.json", "run.log", "solver.f90", "params.py"} <= paths
    assert not paths & {".env", "deploy.pem", ".git/config", "id_rsa", "results.bin", "notes.xyz"}
    assert listed["folder"] == "heat_project" and listed["truncated"] is False


def test_reads_a_line_range_with_line_numbers(connector):
    result = read(connector, "heat2d.py", "18-25").json()
    assert result["path"] == "heat2d.py" and result["kind"] == "code"
    assert result["lines"] == "18-25" and result["total_lines"] == 59
    shown = result["text"].splitlines()
    assert shown[0] == "  ..." and shown[1].startswith("   18| ") and shown[-1] == "  ..."  # gaps are marked
    assert "lap = T[i + 1][j]" in result["text"]
    assert result["truncated"] is True  # not the whole file


def test_a_log_without_a_range_keeps_its_start_problems_and_end(connector):
    result = read(connector, "run.log").json()
    assert result["kind"] == "log"
    assert "inf" in result["text"].lower()
    assert result["text"].splitlines()[0].startswith("    1| ")


def test_long_files_are_capped_and_short_ranges_say_so(connector):
    whole = read(connector, "solver.f90").json()
    assert whole["total_lines"] == 1000 and whole["truncated"] is True
    assert len(whole["text"]) <= 12_000
    past_end = read(connector, "config.json", "50-60").json()
    assert past_end["text"] == "" and "only 9 lines" in past_end["note"]


@pytest.mark.parametrize("path, reason", [
    ("../outside.py", "must not leave the project folder"),
    (".env", "sensitive"),
    ("id_rsa", "sensitive"),
    ("deploy.pem", "sensitive"),
    (".git/config", "sensitive"),
    ("results.bin", "research file type"),
    ("notes.xyz", "research file type"),
    ("missing.py", "not a file"),
])
def test_refuses_what_an_agent_must_not_read(connector, path, reason):
    response = read(connector, path)
    assert response.status_code == 403 and reason in response.json()["detail"]


def test_refuses_local_paths_even_inside_the_folder(connector, project):
    response = read(connector, str(project / "heat2d.py"))
    assert response.status_code == 403 and "relative to the project folder" in response.json()["detail"]


def test_a_symlink_cannot_escape_the_granted_folder(connector, project, tmp_path):
    try:
        os.symlink(tmp_path / "outside.py", project / "link.py")
    except OSError:
        pytest.skip("This computer doesn't allow creating symlinks.")
    response = read(connector, "link.py")
    assert response.status_code == 403 and "outside the granted project folder" in response.json()["detail"]


def test_a_linked_folder_cannot_escape_the_granted_folder(connector, project, tmp_path):
    """On Windows a directory junction needs no special rights, so it's the realistic escape."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "private.py").write_text("SECRET_RESULT = 42\n")
    try:
        os.symlink(elsewhere, project / "linked", target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("This computer doesn't allow creating symlinks.")
        import _winapi
        _winapi.CreateJunction(str(elsewhere), str(project / "linked"))
    response = read(connector, "linked/private.py")
    assert response.status_code == 403 and "outside the granted project folder" in response.json()["detail"]
    listed = connector.client.post("/tools/list_project_files", json={"folder": "heat_project"}).json()
    assert not any(f["path"].startswith("linked/") for f in listed["files"])


def test_only_granted_folders_by_name_and_never_an_ambiguous_one(connector, tmp_path):
    assert read(connector, "heat2d.py", folder="someone_else").status_code == 403
    twin = tmp_path / "copy" / "heat_project"
    twin.mkdir(parents=True)
    connector.registry.grant(twin)
    response = read(connector, "heat2d.py")
    assert response.status_code == 403 and "Two granted folders" in response.json()["detail"]


def test_secrets_are_blanked_and_bad_ranges_refused(connector):
    result = read(connector, "params.py").json()
    assert "sk_live_decoy_value_12345" not in result["text"] and result["redactions"] == 1
    assert read(connector, "heat2d.py", "ten-twenty").status_code == 422
    assert read(connector, "heat2d.py", "30-20").status_code == 422


def test_reading_never_runs_or_changes_anything(connector, project):
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in project.rglob("*") if p.is_file()}
    read(connector, "trap.py")
    read(connector, "heat2d.py", "1-59")
    connector.client.post("/tools/list_project_files", json={"folder": "heat_project"})
    assert not (project / "pwned.txt").exists() and not Path("pwned.txt").exists()
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in project.rglob("*") if p.is_file()} == before


# --- The toolkit in personal chat --------------------------------------------------------------


class ScriptedCreao:
    """Replies in order; with none left, a diagnosis (used to publish a finding)."""

    def __init__(self):
        self.replies, self.calls = [], []

    def run(self, inputs):
        self.calls.append(inputs)
        if not self.replies:
            return DIAGNOSIS
        return SimpleNamespace(reply=self.replies.pop(0), run_id=f"run-{len(self.calls)}")


def tool_block(*requests_):
    return "\n```json\n" + json.dumps({"tool_requests": list(requests_)}) + "\n```"


@pytest.fixture
def lab(tmp_path, connector):
    creao = ScriptedCreao()
    data = tmp_path / "data"
    app = create_app(client_factory=lambda prefix: creao, data_dir=data, jobs=None,
                     chat_setup=lambda: ChatSettings("CHAT", "test"), connector=ConnectorClient("", Local(connector.client)))
    client = TestClient(app)
    headers = {m: {"Authorization": f"Bearer {client.post('/sign-in', json={'member_id': m}).json()['token']}"}
               for m in ("researcher_a", "researcher_b", "leader")}
    return SimpleNamespace(client=client, creao=creao, headers=headers, db=Database(data))


def publish(lab, member="researcher_a", project="heat-diffusion", title=FINDING_TITLE,
            summary="Symptom: max|T| reaches inf. Cause: dt = 0.03 gives r = 0.30 > 0.25. Use dt <= 0.02 when dx = 0.01."):
    job_id = lab.client.post("/diagnose", json={"project_id": project, "symptom": "the run blows up", "use_super_brain": False},
                             headers=lab.headers[member]).json()["job_id"]
    draft_id = lab.client.get(f"/diagnose/{job_id}", headers=lab.headers[member]).json()["draft_id"]
    return lab.client.post(f"/drafts/{draft_id}/approve", json={"title": title, "summary": summary},
                           headers=lab.headers[member]).json()


def chat(lab, replies, message="Why does my heat solver blow up?", member="researcher_a", folder="heat_project"):
    lab.creao.replies = list(replies)
    body = {"project_id": "heat-diffusion", "message": message, **({"folder": folder} if folder else {})}
    job_id = lab.client.post("/personal-agent/chat", json=body, headers=lab.headers[member]).json()["job_id"]
    return lab.client.get(f"/jobs/{job_id}", headers=lab.headers[member]).json()


def test_the_agent_reads_code_and_finds_the_groups_finding_before_answering(lab):
    finding = publish(lab)
    calls_before = len(lab.creao.calls)
    job = chat(lab, [
        "Let me check the update loop and the group's findings." + tool_block(
            {"tool": "list_project_files"},
            {"tool": "read_project_file", "path": "heat2d.py", "lines": "18-25"},
            {"tool": "search_super_brain", "query": "time step CFL blow up"},
            {"tool": "get_simulation_runs"}),
        f"r * lap at heat2d.py:23 is the explicit update; with dt = 0.03, r = 0.30 > 0.25 ({finding['id']}).",
    ])
    assert job["status"] == "completed"
    first, second = lab.creao.calls[calls_before:]
    assert "read_project_file" in first["observed_symptom"] and "tool_requests" in first["observed_symptom"]
    assert "Never execute, compile or test the project's code" in first["observed_symptom"]
    sent = second["observed_symptom"]
    assert "tool_results" in sent and "lap = T[i + 1][j]" in sent and "solver.f90" in sent
    assert FINDING_TITLE in sent and "Researcher A" in sent
    assert "No more tool requests are possible" not in sent  # one more round was still allowed
    reply = job["diagnosis"]["reply"]
    assert reply.startswith("r * lap at heat2d.py:23")
    assert "Checked: listed 6 files in heat_project · read heat2d.py lines 18-25 · " \
           'searched the Super Brain for "time step CFL blow up" (1 findings) · looked at 0 recent runs' in reply
    assert "tool_requests" not in reply and job["diagnosis"]["creao_runs"] == 2
    saved = lab.client.get("/projects/heat-diffusion/personal-chat", headers=lab.headers["researcher_a"]).json()["exchanges"][-1]
    assert [c["tool"] for c in saved["tool_calls"]] == ["list_project_files", "read_project_file",
                                                       "search_super_brain", "get_simulation_runs"]
    assert "lap = T[i + 1][j]" not in json.dumps(saved)  # what was read isn't kept in history


def test_at_most_two_rounds_of_tool_requests_per_message(lab):
    again = tool_block({"tool": "search_super_brain", "query": "stability"})
    job = chat(lab, ["one" + again, "two" + again, "Still not sure." + again])
    assert len(lab.creao.calls) == 3
    assert "No more tool requests are possible" in lab.creao.calls[-1]["observed_symptom"]
    assert job["diagnosis"]["reply"].startswith("Still not sure.") and "tool_requests" not in job["diagnosis"]["reply"]


def test_requests_are_capped_per_round_and_by_size(lab):
    job = chat(lab, [tool_block(*[{"tool": "search_super_brain", "query": f"dt {i}"} for i in range(MAX_TOOL_REQUESTS + 2)]), "ok"])
    calls = job["diagnosis"]["tool_calls"]
    assert [c["ok"] for c in calls] == [True] * MAX_TOOL_REQUESTS + [False, False]
    assert "at most 5 requests" in calls[-1]["summary"]

    big = [{"tool": "read_project_file", "path": "solver.f90", "lines": f"{s}-{s + 399}"} for s in (1, 401, 801)]
    job = chat(lab, [tool_block(*big), "ok"])
    assert [c["ok"] for c in job["diagnosis"]["tool_calls"]] == [True, True, False]
    assert MAX_ROUND_CHARS < 3 * 12_000 and "size limit" in job["diagnosis"]["tool_calls"][-1]["summary"]


def test_refused_and_unknown_requests_come_back_as_errors_not_files(lab):
    job = chat(lab, [tool_block({"tool": "read_project_file", "path": ".env"},
                                {"tool": "read_project_file", "path": "../outside.py"},
                                {"tool": "run_shell", "command": "rm -rf /"},
                                {"tool": "read_project_file"}), "ok"])
    calls = job["diagnosis"]["tool_calls"]
    assert not any(c["ok"] for c in calls)
    assert "sensitive" in calls[0]["summary"] and "leave the project folder" in calls[1]["summary"]
    assert 'unknown tool "run_shell"' in calls[2]["summary"] and "Name the file" in calls[3]["summary"]
    assert "capi_decoy_secret_value_123" not in lab.creao.calls[-1]["observed_symptom"]


def test_without_a_folder_the_agent_cant_read_files_but_can_search(lab):
    publish(lab)
    job = chat(lab, [tool_block({"tool": "read_project_file", "path": "heat2d.py"},
                                {"tool": "search_super_brain", "query": "CFL time step"}), "ok"], folder=None)
    first = lab.creao.calls[-2]["observed_symptom"]
    assert "No project folder is connected" in first and "- list_project_files" not in first
    calls = job["diagnosis"]["tool_calls"]
    assert [c["ok"] for c in calls] == [False, True]
    assert "No project folder is connected" in calls[0]["summary"]


def test_a_stopped_connector_is_reported_to_the_agent(lab, tmp_path):
    class Down:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("refused")
    app = create_app(client_factory=lambda prefix: lab.creao, data_dir=tmp_path / "data",
                     chat_setup=lambda: ChatSettings("CHAT", "test"), connector=ConnectorClient("http://127.0.0.1:1", Down()))
    client = TestClient(app)
    token = client.post("/sign-in", json={"member_id": "researcher_a"}).json()["token"]
    result = client.post("/agent-tools", json={"project_id": "heat-diffusion", "folder": "heat_project",
                                               "tool": "list_project_files"}, headers={"Authorization": f"Bearer {token}"}).json()
    assert result["ok"] is False and "local connector isn't running" in result["error"]


# --- Super Brain search and runs: the researcher's permissions ---------------------------------


def tool(lab, member, name, project="heat-diffusion", **args):
    return lab.client.post("/agent-tools", json={"project_id": project, "folder": "heat_project", "tool": name, "args": args},
                           headers=lab.headers[member])


def test_search_returns_findings_with_project_evidence_and_status(lab):
    finding = publish(lab)
    lab.client.post(f"/findings/{finding['id']}/notes", json={"kind": "detail", "text": "Seen with a 100 x 100 grid."},
                    headers=lab.headers["researcher_b"])
    [found] = tool(lab, "researcher_b", "search_super_brain", query="CFL time step blow up").json()["result"]
    assert found["id"] == finding["id"] and found["project"] == "heat-diffusion" and found["researcher"] == "Researcher A"
    assert found["verification_status"] == "hypothesis" and "r = 0.30 > 0.25" in found["summary"]
    assert found["notes"] == [{"by": "Researcher B", "kind": "detail", "text": "Seen with a 100 x 100 grid."}]


def test_search_never_reaches_a_restricted_project_or_another_group(lab):
    publish(lab, project="wave-propagation", title="Wave solver blows up when the Courant number exceeds 1",
            summary="Restricted: c dt / dx = 1.2 in the leapfrog scheme.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(lab.db.shared()) as conn, conn:
        conn.execute("INSERT INTO workspaces (id, name, created_at) VALUES ('other-lab', 'Another group', ?)", (now,))
        conn.execute("INSERT INTO members (id, workspace_id, display_name, role, created_at) "
                     "VALUES ('outsider', 'other-lab', 'Outsider', 'researcher', ?)", (now,))
        conn.execute("INSERT INTO projects (id, workspace_id, name, created_at) VALUES ('their-heat', 'other-lab', 'Their heat', ?)", (now,))
        conn.execute("INSERT INTO project_members (project_id, member_id, added_at) VALUES ('their-heat', 'outsider', ?)", (now,))
        conn.execute("INSERT INTO approvals (id, workspace_id, project_id, member_id, draft_id, approved_at) "
                     "VALUES ('their-approval', 'other-lab', 'their-heat', 'outsider', 'their-draft', ?)", (now,))
        conn.execute("INSERT INTO shared_findings (id, workspace_id, project_id, category, title, summary, verification_status, "
                     "contributed_by, approval_id, created_at) VALUES ('their-finding', 'other-lab', 'their-heat', "
                     "'error_diagnosis', 'Courant number and time step blow up', 'Private to another group.', 'verified', "
                     "'outsider', 'their-approval', ?)", (now,))
    found_by_b = tool(lab, "researcher_b", "search_super_brain", query="Courant number time step blow up").json()["result"]
    assert found_by_b == []
    found_by_a = tool(lab, "researcher_a", "search_super_brain", query="Courant number time step blow up").json()["result"]
    assert [f["project"] for f in found_by_a] == ["wave-propagation"]  # A is a member there, never of their-heat


def test_search_is_read_only(lab):
    publish(lab)
    tables = ("shared_findings", "approvals", "finding_links", "finding_notes", "auto_shares")
    with closing(lab.db.shared()) as conn:
        before = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables]
    tool(lab, "researcher_a", "search_super_brain", query="CFL time step")
    with closing(lab.db.shared()) as conn:
        assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables] == before


def test_runs_are_the_researchers_own(lab):
    run = lab.client.post("/simulation-runs", json={"project_id": "heat-diffusion", "run_label": "python heat2d.py config.json"},
                          headers=lab.headers["researcher_a"]).json()
    alert = {"severity": "critical", "check": "nan_inf", "message": "max|T| became inf at step 2223 (line 24)",
             "metric": "max|T|", "line_number": 24, "line": "step  2223  max|T| = inf", "step": 2223}
    points = [{"step": s, "values": {"max|T|": v}} for s, v in ((100, 276.85), (200, 2.75e14), (2223, None))]
    lab.client.post(f"/simulation-runs/{run['id']}/report", json={"points": points, "alerts": [alert], "stopped": True},
                    headers=lab.headers["researcher_a"])

    [listed] = tool(lab, "researcher_a", "get_simulation_runs").json()["result"]
    assert listed["id"] == run["id"] and listed["alerts"]["critical"] == 1 and listed["last_step"] == 2223
    detail = tool(lab, "researcher_a", "get_simulation_runs", run_id=run["id"]).json()
    assert detail["result"]["alerts"][0]["step"] == 2223
    assert detail["result"]["numbers"]["max|T|"]["first_non_finite_step"] == 2223
    assert 'looked at run "python heat2d.py config.json" (1 alerts)' == detail["summary"]

    assert tool(lab, "researcher_b", "get_simulation_runs").json()["result"] == []
    hidden = tool(lab, "researcher_b", "get_simulation_runs", run_id=run["id"]).json()
    assert hidden["ok"] is False and "No run with that ID" in hidden["error"]


def test_the_tool_endpoint_checks_sign_in_membership_and_input(lab):
    assert lab.client.post("/agent-tools", json={"project_id": "heat-diffusion", "tool": "search_super_brain"}).status_code == 401
    assert tool(lab, "researcher_b", "search_super_brain", project="wave-propagation", query="x y").status_code == 403
    bad = lab.client.post("/agent-tools", json={"project_id": "heat-diffusion", "folder": "C:\\Users\\me\\heat",
                                                "tool": "list_project_files"}, headers=lab.headers["researcher_a"])
    assert bad.status_code == 422  # a folder is named, never given as a path
    assert lab.client.post("/personal-agent/chat", json={"project_id": "heat-diffusion", "message": "hi", "folder": "../x"},
                           headers=lab.headers["researcher_a"]).status_code == 422
