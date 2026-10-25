"""ExpandRG as an installed package: the expandrg command, where its files go, and the dashboard
served without Node (static pages, with /api and /connector passed on)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from expandrg import __version__, dashboard, paths
from expandrg.__main__ import main


def test_the_command_shows_its_version_and_lists_what_it_does(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"ExpandRG {__version__}"
    with pytest.raises(SystemExit):
        main(["--help"])
    usage = capsys.readouterr().out
    for command in ("start", "stop", "setup", "add-project"):
        assert command in usage


def test_the_command_runs_setup_and_start(monkeypatch):
    seen = []
    monkeypatch.setattr("expandrg.setup_group.run_add_project", lambda: seen.append("add-project") or 0)
    monkeypatch.setattr("expandrg.launcher.start", lambda **kw: seen.append(kw) or 0)
    monkeypatch.setattr("expandrg.launcher.stop_running", lambda: seen.append("stop") or 0)
    assert main(["add-project"]) == 0
    assert main(["start", "--group", "fluids-lab", "--no-browser"]) == 0
    assert main(["stop"]) == 0
    assert seen == ["add-project", {"group": "fluids-lab", "fresh": False, "check": False, "no_browser": True}, "stop"]


def test_a_stopped_setup_saves_nothing(monkeypatch, capsys):
    def interrupted():
        raise KeyboardInterrupt
    monkeypatch.setattr("expandrg.setup_group.run", interrupted)
    assert main(["setup"]) == 1
    assert "Nothing was saved." in capsys.readouterr().out


def test_start_refuses_while_expandrg_is_already_running(monkeypatch, capsys):
    from expandrg import launcher
    monkeypatch.setattr(launcher, "running_group", lambda: "fluids-lab")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: pytest.fail("nothing should start"))
    assert launcher.start(group="optics-lab", check=True) == 1
    out = capsys.readouterr().out
    assert "already running (group fluids-lab)" in out and "Stop it first: python run_demo.py --stop" in out


def test_installed_files_live_in_expandrg_home_and_a_checkout_keeps_its_own(monkeypatch, tmp_path):
    assert paths.FROM_SOURCE  # these tests run from the checkout
    assert paths.demo_data_dir() == paths.SOURCE / "backend" / "data"
    assert paths.demo_env_file() == paths.SOURCE / "backend" / ".env"
    assert paths.command("add-project") == "python setup_expandrg.py --add-project"

    monkeypatch.setattr(paths, "FROM_SOURCE", False)
    monkeypatch.setenv("EXPANDRG_HOME", str(tmp_path))
    assert paths.demo_data_dir() == tmp_path / "demo" / "data"
    assert paths.demo_env_file() == tmp_path / "demo.env"
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.command("start") == "expandrg start"


def upstream(name):
    """A fake backend or connector that echoes what reached it."""
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/down"):
            raise httpx.ConnectError("refused")
        body = {"service": name, "method": request.method, "path": request.url.path, "query": request.url.query.decode(),
                "body": request.content.decode(), "auth": request.headers.get("authorization")}
        return httpx.Response(201, json=body, headers={"x-from": name})
    return answer


@pytest.fixture
def served(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>ExpandRG</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('dashboard')", encoding="utf-8")

    class Routed(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return upstream("backend" if request.url.port == 8000 else "connector")(request)

    return TestClient(dashboard.create_app(tmp_path, "http://127.0.0.1:8000", "http://127.0.0.1:8001", transport=Routed()))


def test_the_dashboard_pages_are_served(served):
    page = served.get("/")
    assert page.status_code == 200 and "<title>ExpandRG</title>" in page.text
    assert served.get("/assets/app.js").text == "console.log('dashboard')"
    assert served.get("/nothing-here.js").status_code == 404


def test_api_and_connector_calls_are_passed_on_unchanged(served):
    sent = served.post("/api/projects?scope=all&x=a%20b", json={"name": "Pipe flow"},
                       headers={"Authorization": "Bearer t0ken"})
    assert sent.status_code == 201 and sent.headers["x-from"] == "backend"
    echoed = sent.json()
    assert (echoed["method"], echoed["path"], echoed["query"]) == ("POST", "/projects", "scope=all&x=a%20b")
    assert json.loads(echoed["body"]) == {"name": "Pipe flow"} and echoed["auth"] == "Bearer t0ken"
    assert served.delete("/connector/watches/w1").json()["service"] == "connector"
    assert served.get("/connector/watches/w1").json()["path"] == "/watches/w1"


def test_a_service_that_isnt_running_gives_a_readable_error(served):
    down = served.get("/api/down")
    assert down.status_code == 502 and down.json() == {"detail": "The ExpandRG backend isn't answering."}
