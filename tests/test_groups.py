"""Setting up a new research group: the terminal setup, its folder and its own Super Brain.
CREAO is faked; nothing here makes a network call."""
import json
import os
from contextlib import closing
from types import SimpleNamespace

import pytest
import requests
from dotenv import dotenv_values
from fastapi.testclient import TestClient

import setup_expandrg
from backend.api.app import create_app
from backend.creao.client import personal_agent
from backend.db.database import Database
from backend.workspaces.demo import seed_demo
from backend.workspaces.groups import Agent, GroupError, check_app, check_key, groups_home, group_environment

HUB = ("11111111-1111-1111-1111-111111111111", "capi_superbrain_key_0001")
ANA = ("22222222-2222-2222-2222-222222222222", "capi_ana_agent_key_0002")
BEN = ("33333333-3333-3333-3333-333333333333", "capi_ben_agent_key_0003")


class Terminal:
    """Scripted answers, hidden ones separately, and everything printed."""

    def __init__(self, answers, secrets, key_results=None):
        self.answers, self.secrets, self.printed = list(answers), list(secrets), []
        self.key_results = list(key_results or [])
        self.checked_keys, self.checked_apps = [], []

    def ask(self, prompt):
        self.printed.append(prompt)
        return self.answers.pop(0)

    def ask_secret(self, prompt):
        self.printed.append(prompt)
        return self.secrets.pop(0)

    def say(self, text=""):
        self.printed.append(text)

    def key_checker(self, agent):
        self.checked_keys.append(agent.key)
        return self.key_results.pop(0) if self.key_results else "ok"

    def app_checker(self, agent):
        self.checked_apps.append(agent.app_id)
        return "ok"

    def run(self):
        return setup_expandrg.run(self.ask, self.ask_secret, self.say, self.key_checker, self.app_checker)

    @property
    def text(self):
        return "\n".join(self.printed)


LEADER_ANSWERS = ["y", "Fluids Lab", "Dr. Maya Lee", HUB[0], "Ana Ruiz", ANA[0], "Ben Okafor", BEN[0], "",
                  "Channel flow solver", "", "n", "y"]


def test_the_leader_sets_up_a_group_with_its_own_super_brain():
    terminal = Terminal(LEADER_ANSWERS, [HUB[1], ANA[1], BEN[1]])
    assert terminal.run() == 0
    folder = groups_home() / "fluids-lab"
    assert "agent.creao.ai" in terminal.text and "API Trigger" in terminal.text  # where to find the ID and key
    assert "python run_demo.py --group fluids-lab" in terminal.text
    assert terminal.checked_keys == [HUB[1], ANA[1], BEN[1]] and terminal.checked_apps == []
    assert HUB[1] not in terminal.text and "capi_****0001" in terminal.text  # keys are never shown in full

    env = dotenv_values(folder / "group.env")
    assert (env["CREAO_HUB_APP_ID"], env["CREAO_HUB_KEY"]) == HUB
    assert (env["CREAO_AGENT_1_APP_ID"], env["CREAO_AGENT_1_KEY"]) == ANA
    assert (env["CREAO_AGENT_2_APP_ID"], env["CREAO_AGENT_2_KEY"]) == BEN
    described = (folder / "group.json").read_text(encoding="utf-8")
    assert "capi_" not in described
    group = json.loads(described)
    assert group["leader"] == {"id": "dr-maya-lee", "name": "Dr. Maya Lee"}
    assert [r["id"] for r in group["researchers"]] == ["ana-ruiz", "ben-okafor"]
    assert group["projects"][0]["id"] == "channel-flow-solver"

    with closing(Database(folder / "data").shared()) as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM workspaces")] == ["fluids-lab"]
        assert {(r["id"], r["role"]) for r in conn.execute("SELECT id, role FROM members")} == {
            ("dr-maya-lee", "leader"), ("ana-ruiz", "researcher"), ("ben-okafor", "researcher")}
        assert {(r["member_id"], r["creao_env_prefix"]) for r in conn.execute("SELECT * FROM agents")} == {
            ("ana-ruiz", "AGENT_1"), ("ben-okafor", "AGENT_2")}
        assert {r[0] for r in conn.execute("SELECT member_id FROM project_members WHERE project_id = 'channel-flow-solver'")} == {
            "dr-maya-lee", "ana-ruiz", "ben-okafor"}
        assert conn.execute("SELECT COUNT(*) FROM shared_findings").fetchone()[0] == 0  # a new, empty Super Brain


def test_a_researcher_is_sent_to_their_leader_and_nothing_is_made():
    terminal = Terminal(["n"], [])
    assert terminal.run() == 0
    assert "Only the group leader sets up a group" in terminal.text
    assert "Wait until your leader gives you the group ID" in terminal.text
    assert "app ID:" not in terminal.text and "typing is hidden" not in terminal.text
    assert terminal.checked_keys == [] and terminal.checked_apps == []
    assert not groups_home().exists() or not any(groups_home().iterdir())


def test_the_role_question_retries_a_typo_before_asking_for_any_credentials():
    terminal = Terminal(["maybe", "no"], [])
    assert terminal.run() == 0
    assert terminal.text.count("Are you the group leader?") == 2
    assert "Answer y or n." in terminal.text
    assert terminal.checked_keys == []
    assert not groups_home().exists()


def test_bad_ids_and_keys_are_asked_again():
    answers = ["y", "Fluids Lab", "Maya", "not-an-id", HUB[0], "Ana", ANA[0], "", "Channel flow", "n", "y"]
    secrets = ["cr_sk_paid_developer_key_x", "capi_wrong_key_entered_1", HUB[1], ANA[1]]
    terminal = Terminal(answers, secrets, key_results=["rejected", "ok", "ok"])
    assert terminal.run() == 0
    assert "36 characters" in terminal.text
    assert "paid developer key (cr_sk_)" in terminal.text
    assert "CREAO refused this key" in terminal.text
    assert dotenv_values(groups_home() / "fluids-lab" / "group.env")["CREAO_HUB_KEY"] == HUB[1]


def test_the_first_project_can_leave_researchers_out_and_names_are_unique():
    answers = ["y", "Fluids Lab", "Ana", HUB[0], "Ana", ANA[0], "Ben", BEN[0], "", "Channel flow", "9", "2", "n", "y"]
    assert Terminal(answers, [HUB[1], ANA[1], BEN[1]]).run() == 0
    group = json.loads((groups_home() / "fluids-lab" / "group.json").read_text(encoding="utf-8"))
    assert group["leader"]["id"] == "ana" and group["researchers"][0]["id"] == "ana-2"  # same name, distinct IDs
    assert group["projects"][0]["members"] == ["ana", "ben"]


def test_an_existing_group_name_is_refused_and_not_saving_saves_nothing():
    assert Terminal(LEADER_ANSWERS, [HUB[1], ANA[1], BEN[1]]).run() == 0
    answers = ["y", "Fluids Lab", "Fluids Lab 2", "Maya", HUB[0], "Ana", ANA[0], "", "Channel flow", "n", "n"]
    terminal = Terminal(answers, [HUB[1], ANA[1]])
    assert terminal.run() == 1
    assert 'A group called "fluids-lab" already exists' in terminal.text
    assert "Nothing was saved." in terminal.text and not (groups_home() / "fluids-lab-2").exists()


def test_a_started_group_uses_its_own_data_and_keys_never_the_demo(monkeypatch):
    Terminal(LEADER_ANSWERS, [HUB[1], ANA[1], BEN[1]]).run()
    for name, value in group_environment("fluids-lab").items():
        monkeypatch.setenv(name, value)
    client = TestClient(create_app(client_factory=lambda prefix: None))  # the backend as run_demo.py --group starts it
    people = {m["id"] for m in client.get("/demo/members").json()}
    assert people == {"dr-maya-lee", "ana-ruiz", "ben-okafor"}  # no demo Leader, A, B or C
    token = client.post("/sign-in", json={"member_id": "ana-ruiz"}).json()["token"]
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert [p["id"] for p in me["projects"]] == ["channel-flow-solver"] and me["agent"]["name"] == "Agent 1"

    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("CREAO_")})
    agent = personal_agent("AGENT_1", env_file=groups_home() / "fluids-lab" / "group.env")
    assert agent.app_id == ANA[0]


def test_the_demo_is_never_added_to_a_groups_database():
    Terminal(LEADER_ANSWERS, [HUB[1], ANA[1], BEN[1]]).run()
    db = Database(groups_home() / "fluids-lab" / "data", seed=seed_demo)
    with closing(db.shared()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM members WHERE id = 'researcher_a'").fetchone()[0] == 0


def test_an_unknown_group_lists_the_ones_there_are():
    Terminal(LEADER_ANSWERS, [HUB[1], ANA[1], BEN[1]]).run()
    with pytest.raises(GroupError, match="Groups on this computer: fluids-lab"):
        group_environment("nope")


def test_key_and_app_checks_read_creaos_answers():
    def http(status=200, error=None):
        def call(*args, **kwargs):
            if error:
                raise error
            return SimpleNamespace(status_code=status, ok=status < 400)
        return SimpleNamespace(get=call, post=call)
    agent = Agent(*HUB)
    assert check_key(agent, http=http(200)) == "ok"
    assert check_key(agent, http=http(401)) == "rejected"
    assert check_key(agent, http=http(error=requests.ConnectionError())) == "unreachable"
    assert check_app(agent, http=http(404)) == "not_found"
    assert check_app(agent, http=http(201)) == "ok"
