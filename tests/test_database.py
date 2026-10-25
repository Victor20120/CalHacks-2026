"""Tests for the databases, demo data, private memory and prototype sign-in."""
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.auth.permissions import PermissionDenied
from backend.db.database import Database
from backend.db.schema import PRIVATE_TABLES, SHARED_TABLES
from backend.memory.private_memory import PrivateMemory
from backend.workspaces.demo import seed_demo
from backend.workspaces.store import is_project_member, projects_for_member

EXPECTED_DEMO_COUNTS = {
    "workspaces": 1, "members": 4, "projects": 2, "project_members": 6, "agents": 3,
    "approvals": 0, "shared_findings": 0, "finding_links": 0, "handoff_events": 0, "handoff_records": 0,
    "simulation_runs": 0, "run_points": 0, "run_alerts": 0, "auto_shares": 0, "finding_notes": 0,
    "worksheets": 0, "worksheet_items": 0, "finding_runs": 0, "finding_comments": 0,
    "worksheet_weeks": 0, "worksheet_in_week": 0,
    "context_artifacts": 0, "context_links": 0, "context_events": 0,
}
MARKER = "PRIVATE-MARKER-7f3c: suspect ghost-cell sign error in my branch"


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "data", seed=seed_demo)


def counts(db):
    with closing(db.shared()) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in SHARED_TABLES}


def offboard(db, member_id):
    with closing(db.shared()) as conn:
        conn.execute("UPDATE members SET status = 'offboarded' WHERE id = ?", (member_id,))
        conn.commit()


# --- Demo data -------------------------------------------------------------


def test_a_fresh_database_has_the_demo_data(db):
    assert counts(db) == EXPECTED_DEMO_COUNTS


def test_loading_the_demo_twice_creates_no_duplicates(db):
    counts(db)
    with closing(db.shared()) as conn:
        seed_demo(conn)
        seed_demo(conn)

    assert counts(db) == EXPECTED_DEMO_COUNTS


def test_reset_deletes_everything_and_starts_again(db):
    PrivateMemory(db, "researcher_a").add_episode("heat-diffusion", "note", MARKER)

    deleted = db.reset()

    assert db.shared_path in deleted and db.private_path("researcher_a") in deleted
    assert counts(db) == EXPECTED_DEMO_COUNTS
    assert PrivateMemory(db, "researcher_a").episodes("heat-diffusion") == []


def test_researcher_b_is_not_in_the_wave_project(db):
    with closing(db.shared()) as conn:
        a_projects = [p["id"] for p in projects_for_member(conn, "researcher_a")]
        b_projects = [p["id"] for p in projects_for_member(conn, "researcher_b")]
        assert a_projects == ["heat-diffusion", "wave-propagation"]
        assert b_projects == ["heat-diffusion"]
        assert not is_project_member(conn, "researcher_b", "wave-propagation")


def test_the_database_rejects_invalid_values(db):
    with closing(db.shared()) as conn:
        with pytest.raises(sqlite3.IntegrityError):  # unknown member (foreign key)
            conn.execute("INSERT INTO project_members VALUES ('heat-diffusion', 'nobody', 'now')")
        with pytest.raises(sqlite3.IntegrityError):  # role not allowed
            conn.execute("INSERT INTO members VALUES ('x', 'demo-lab', 'X', 'admin', 'active', 'now')")


# --- Private memory stays private ------------------------------------------


def test_private_and_shared_tables_never_overlap(db):
    PrivateMemory(db, "researcher_a").add_episode("heat-diffusion", "note", MARKER)

    with closing(db.shared()) as conn:
        shared_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    with closing(db.private("researcher_a")) as conn:
        private_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert shared_tables == set(SHARED_TABLES)
    assert private_tables == set(PRIVATE_TABLES)
    assert db.shared_path != db.private_path("researcher_a")


def test_a_private_episode_never_appears_in_the_shared_database(db):
    PrivateMemory(db, "researcher_a").add_episode("heat-diffusion", "diagnosis", MARKER, details=MARKER)

    with closing(db.shared()) as conn:
        for table in SHARED_TABLES:
            for row in conn.execute(f"SELECT * FROM {table}"):
                assert MARKER not in " ".join(str(v) for v in row)
    assert MARKER.encode() not in db.shared_path.read_bytes()  # not even in the raw file


def test_each_researcher_has_their_own_private_memory(db):
    PrivateMemory(db, "researcher_a").add_episode("heat-diffusion", "note", MARKER)

    assert PrivateMemory(db, "researcher_b").episodes("heat-diffusion") == []
    assert db.private_path("researcher_a") != db.private_path("researcher_b")


def test_private_memory_is_split_by_project(db):
    memory = PrivateMemory(db, "researcher_a")
    memory.add_episode("heat-diffusion", "incident", "heat run blew up")
    memory.add_episode("wave-propagation", "incident", "wave run stalled")

    heat = memory.episodes("heat-diffusion")

    assert [e["summary"] for e in heat] == ["heat run blew up"]


def test_private_memory_requires_project_membership(db):
    memory = PrivateMemory(db, "researcher_b")

    with pytest.raises(PermissionDenied):
        memory.add_episode("wave-propagation", "note", "should not be stored")
    with pytest.raises(PermissionDenied):
        memory.episodes("wave-propagation")


def test_offboarded_member_loses_project_access(db):
    offboard(db, "researcher_b")

    with closing(db.shared()) as conn:
        assert projects_for_member(conn, "researcher_b") == []
    with pytest.raises(PermissionDenied):
        PrivateMemory(db, "researcher_b").episodes("heat-diffusion")


def test_episode_kind_and_status_are_checked(db):
    memory = PrivateMemory(db, "researcher_a")

    with pytest.raises(ValueError):
        memory.add_episode("heat-diffusion", "gossip", "x")
    with pytest.raises(ValueError):
        memory.add_episode("heat-diffusion", "note", "x", status="proven")


def test_member_ids_cannot_escape_the_data_folder(db):
    with pytest.raises(ValueError):
        db.private_path("../../etc")
    with pytest.raises(ValueError):
        PrivateMemory(db, "Researcher A")


# --- Prototype sign-in -----------------------------------------------------


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(client_factory=lambda: None, data_dir=tmp_path / "data"))


def sign_in(client, member_id):
    return {"Authorization": f"Bearer {client.post('/sign-in', json={'member_id': member_id}).json()['token']}"}


def test_demo_members_can_be_listed(client):
    members = client.get("/demo/members").json()

    assert [m["id"] for m in members] == ["leader", "researcher_a", "researcher_b", "researcher_c"]


def test_signed_in_researchers_see_only_their_projects(client):
    a = client.get("/me", headers=sign_in(client, "researcher_a")).json()
    b = client.get("/me", headers=sign_in(client, "researcher_b")).json()

    assert a["member"]["display_name"] == "Researcher A"
    assert [p["id"] for p in a["projects"]] == ["heat-diffusion", "wave-propagation"]
    assert [p["id"] for p in b["projects"]] == ["heat-diffusion"]


def test_requests_without_a_valid_sign_in_are_refused(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_unknown_member_cannot_sign_in(client):
    assert client.post("/sign-in", json={"member_id": "mallory"}).status_code == 404


def test_offboarding_takes_effect_immediately(client, tmp_path):
    headers = sign_in(client, "researcher_b")
    offboard(Database(tmp_path / "data"), "researcher_b")

    assert client.get("/me", headers=headers).status_code == 403
    assert client.post("/sign-in", json={"member_id": "researcher_b"}).status_code == 403
