"""The launcher's stop command picks only ExpandRG's processes, and the backend says which group it serves."""
from fastapi.testclient import TestClient

from backend.api.app import create_app
from expandrg import launcher


def test_stop_picks_launchers_backend_connector_and_this_dashboard_only():
    frontend = str(launcher.SOURCE / "frontend")
    listing = [
        (10, "python.exe -u run_demo.py --no-browser"),
        (11, "python.exe -m uvicorn backend.api.app:app --port 8000"),
        (12, "python.exe -m local_connector serve"),
        (13, f"node {frontend}\\node_modules\\.bin\\..\\vite\\bin\\vite.js"),
        (14, "node C:\\other-app\\node_modules\\vite\\bin\\vite.js"),  # someone else's dev server
        (15, "python.exe run_demo.py --stop"),  # the stop command itself
        (16, "code.exe --some-flag"),
        (17, "python.exe -u run_demo.py --group fluids-lab"),
    ]
    assert launcher.expandrg_processes(listing, own={99}) == [10, 11, 12, 13, 17]
    assert launcher.expandrg_processes(listing, own={10, 11}) == [12, 13, 17]


def test_stop_also_picks_the_installed_command_and_packaged_dashboard():
    listing = [
        (20, "C:\\venv\\Scripts\\python.exe -m expandrg start --no-browser --group fluids-lab"),
        (21, '"C:\\venv\\Scripts\\expandrg.exe" start'),
        (22, "C:\\venv\\Scripts\\python.exe -m uvicorn expandrg.dashboard:app --port 5173"),
        (23, "C:\\venv\\Scripts\\python.exe -m expandrg stop"),  # the stop command itself
        (24, "C:\\venv\\Scripts\\expandrg.exe stop"),
        (25, "python.exe C:\\work\\expandrg\\analysis.py"),  # a project that happens to be called that
    ]
    assert launcher.expandrg_processes(listing, own=set()) == [20, 21, 22]


def test_the_backend_says_which_group_it_serves(tmp_path, monkeypatch):
    client = TestClient(create_app(client_factory=lambda prefix: None, data_dir=tmp_path / "data"))
    assert client.get("/health").json() == {"status": "ok", "group": "demo"}
    monkeypatch.setenv("EXPANDRG_GROUP", "fluids-lab")
    assert client.get("/health").json()["group"] == "fluids-lab"
