"""Research groups set up with `expandrg setup` (`python setup_expandrg.py` in a checkout), each
with its own Super Brain.

The demo group (Leader, Researchers A, B and C) stays as it is. A new group lives in
its own folder on this computer, $EXPANDRG_HOME/groups/<id>/ (default ~/.expandrg):

    group.env    its CREAO app IDs and keys: the Super Brain and one agent per researcher
    group.json   its name, people and projects (no keys)
    data/        its databases: a new, empty Super Brain and each researcher's private memory

`python run_demo.py --group <id>` starts ExpandRG on that folder instead of the demo.
More projects are added with `python setup_expandrg.py --add-project` or the leader's
Projects page; both update group.json too, since VS Code's Set up lists projects from it.
CREAO agents can't be created from here (that needs a paid developer key), so the
leader makes them in agent.creao.ai and pastes each one's app ID and key.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from backend.db.database import Database
from backend.workspaces.store import InvalidProject, create_project
from expandrg.paths import command

DEFAULT_BASE_URL = "https://agent.creao.ai/api/v1"
APP_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class GroupError(Exception):
    """A group can't be set up as asked; the message says why."""


@dataclass
class Agent:
    app_id: str
    key: str


@dataclass
class Person:
    name: str
    agent: Agent | None = None  # researchers have one; the leader doesn't
    id: str = ""


@dataclass
class GroupSpec:
    name: str
    leader: Person
    super_brain: Agent
    researchers: list[Person]
    project: str
    project_members: list[int] = field(default_factory=list)  # indexes into researchers; empty = all


def groups_home() -> Path:
    home = os.environ.get("EXPANDRG_HOME") or Path.home() / ".expandrg"
    return Path(home) / "groups"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def app_id_problem(app_id: str) -> str | None:
    if not APP_ID.match(app_id.strip()):
        return "An app ID has 36 characters, like 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f."
    return None


def key_problem(key: str) -> str | None:
    key = key.strip()
    if key.startswith("cr_sk_"):
        return "That's a paid developer key (cr_sk_). Use the agent's API Trigger key, which starts with capi_."
    if not key.startswith("capi_") or len(key) < 20:
        return "An agent's key starts with capi_. Copy all of it."
    return None


def check_key(agent: Agent, base_url: str = DEFAULT_BASE_URL, http=requests) -> str:
    """'ok' if CREAO accepts the key, 'rejected' if it doesn't, 'unreachable' if CREAO can't be
    reached. Uses no credits. CREAO answers this for any app ID, so it can't check the app ID."""
    try:
        response = http.get(f"{base_url.rstrip('/')}/apps/{agent.app_id}/runs", params={"limit": 1},
                            headers={"Authorization": f"Bearer {agent.key}"}, timeout=20)
    except requests.RequestException:
        return "unreachable"
    if response.status_code in (401, 403):
        return "rejected"
    return "ok" if response.ok else "unreachable"


def check_app(agent: Agent, base_url: str = DEFAULT_BASE_URL, http=requests) -> str:
    """'ok' if the app exists, 'not_found' if it doesn't, 'unreachable' otherwise. This starts a
    short CREAO run, so it uses a little credit; the setup asks first."""
    try:
        response = http.post(f"{base_url.rstrip('/')}/apps/{agent.app_id}/runs",
                             headers={"Authorization": f"Bearer {agent.key}"}, timeout=30,
                             json={"inputs": {"project_path": "ExpandRG setup check",
                                              "observed_symptom": "Setup check from ExpandRG. Reply OK; nothing else is needed.",
                                              "focus_areas": "all", "analysis_depth": "triage"}})
    except requests.RequestException:
        return "unreachable"
    if response.status_code == 404:
        return "not_found"
    return "ok" if response.ok else "unreachable"


def assign_ids(spec: GroupSpec) -> None:
    """Member IDs from names (lowercase, dashes), unique within the group."""
    taken: set[str] = set()
    for person in [spec.leader, *spec.researchers]:
        base = slugify(person.name) or "member"
        candidate, n = base, 2
        while candidate in taken:
            candidate, n = f"{base}-{n}", n + 1
        person.id = candidate
        taken.add(candidate)


def create_group(spec: GroupSpec, base_url: str = DEFAULT_BASE_URL) -> Path:
    """Write the group's folder: its keys, its description and a new, empty Super Brain.
    Refuses to overwrite a group that already exists."""
    group_id = slugify(spec.name)
    if not group_id:
        raise GroupError("Give the group a name with letters or digits.")
    if not spec.researchers:
        raise GroupError("Add at least one researcher.")
    for agent in [spec.super_brain, *(r.agent for r in spec.researchers)]:
        if agent is None or app_id_problem(agent.app_id) or key_problem(agent.key):
            raise GroupError("Every agent needs a valid app ID and capi_ key.")
    folder = groups_home() / group_id
    if folder.exists():
        raise GroupError(f'A group called "{group_id}" already exists in {folder}. Choose another name.')
    assign_ids(spec)
    project_id = slugify(spec.project) or "first-project"
    if len(project_id) < 2:
        project_id = f"{project_id}-project"
    members = [spec.researchers[i] for i in spec.project_members] if spec.project_members else spec.researchers

    folder.mkdir(parents=True)
    lines = ["# CREAO app IDs and keys for this research group. Keep this file private.",
             f"CREAO_BASE_URL={base_url}",
             f"CREAO_HUB_APP_ID={spec.super_brain.app_id.strip()}",
             f"CREAO_HUB_KEY={spec.super_brain.key.strip()}"]
    for n, researcher in enumerate(spec.researchers, start=1):
        lines += [f"# {researcher.name}",
                  f"CREAO_AGENT_{n}_APP_ID={researcher.agent.app_id.strip()}",
                  f"CREAO_AGENT_{n}_KEY={researcher.agent.key.strip()}"]
    env_file = folder / "group.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_file, 0o600)  # only the owner can read it (where the system supports it)
    except OSError:
        pass

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db = Database(folder / "data")
    with closing(db.shared()) as conn, conn:
        conn.execute("INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)", (group_id, spec.name.strip(), now))
        conn.execute("INSERT INTO members (id, workspace_id, display_name, role, created_at) VALUES (?, ?, ?, 'leader', ?)",
                     (spec.leader.id, group_id, spec.leader.name.strip(), now))
        for n, researcher in enumerate(spec.researchers, start=1):
            conn.execute("INSERT INTO members (id, workspace_id, display_name, role, created_at) VALUES (?, ?, ?, 'researcher', ?)",
                         (researcher.id, group_id, researcher.name.strip(), now))
            conn.execute("INSERT INTO agents (id, member_id, creao_env_prefix, created_at) VALUES (?, ?, ?, ?)",
                         (f"agent-{researcher.id}", researcher.id, f"AGENT_{n}", now))
    with closing(db.shared()) as conn:
        create_project(conn, group_id, project_id, spec.project.strip(), "", [spec.leader.id, *(m.id for m in members)])

    (folder / "group.json").write_text(json.dumps({
        "id": group_id, "name": spec.name.strip(), "created_at": now,
        "leader": {"id": spec.leader.id, "name": spec.leader.name.strip()},
        "researchers": [{"id": r.id, "name": r.name.strip(), "agent_slot": f"AGENT_{n}"} for n, r in enumerate(spec.researchers, 1)],
        "projects": [{"id": project_id, "name": spec.project.strip(), "members": [spec.leader.id, *(m.id for m in members)]}],
    }, indent=2), encoding="utf-8")
    return folder


def group_environment(group_id: str) -> dict[str, str]:
    """The environment that points ExpandRG at a group instead of the demo."""
    folder = groups_home() / group_id
    if not (folder / "group.json").exists():
        known = ", ".join(sorted(p.name for p in groups_home().glob("*") if (p / "group.json").exists())) or "none yet"
        raise GroupError(f'No group called "{group_id}". Groups on this computer: {known}. Make one with: {command("setup")}')
    return {"EXPANDRG_DATA_DIR": str(folder / "data"), "EXPANDRG_ENV_FILE": str(folder / "group.env"),
            "EXPANDRG_GROUP": group_id}


def describe(group_id: str) -> dict:
    return json.loads((groups_home() / group_id / "group.json").read_text(encoding="utf-8"))


def list_groups() -> list[dict]:
    """The groups set up on this computer, by ID. A group file that can't be read is left out."""
    groups = []
    for folder in sorted(groups_home().glob("*")) if groups_home().exists() else []:
        try:
            groups.append(describe(folder.name))
        except (OSError, ValueError):
            pass
    return groups


def project_id_for(name: str) -> str | None:
    """A project's ID from its name (lowercase, dashes), or None when the name has too few letters or digits."""
    project_id = slugify(name)
    return project_id if len(project_id) >= 2 else None


def active_researchers(group_id: str) -> list[dict]:
    """The group's researchers who haven't been offboarded: {"id", "name"}, in the order they were added."""
    group_environment(group_id)  # an unknown group says which ones there are
    with closing(Database(groups_home() / group_id / "data").shared()) as conn:
        return [{"id": r["id"], "name": r["display_name"]} for r in conn.execute(
            "SELECT id, display_name FROM members WHERE role = 'researcher' AND status = 'active' ORDER BY rowid")]


def add_project(group_id: str, name: str, member_ids: list[str] | None = None) -> dict:
    """A new project in a group set up on this computer, with its leader and the given researchers
    (default: every active researcher). Saved in the group's database and in group.json."""
    group_environment(group_id)
    project_id = project_id_for(name)
    if project_id is None:
        raise GroupError("Use at least two letters or digits in the project name.")
    members = member_ids or [r["id"] for r in active_researchers(group_id)]
    with closing(Database(groups_home() / group_id / "data").shared()) as conn:
        try:
            project = create_project(conn, group_id, project_id, name, "", [describe(group_id)["leader"]["id"], *members])
        except InvalidProject as exc:
            raise GroupError(str(exc)) from None
    note_project(group_id, project)
    return project


def note_project(group_id: str, project: dict) -> None:
    """Records a project in group.json, where VS Code's Set up reads a group's projects. Does
    nothing for a group without one (the demo)."""
    path = groups_home() / group_id / "group.json"
    if not path.exists():
        return
    group = json.loads(path.read_text(encoding="utf-8"))
    entry = {"id": project["id"], "name": project["name"], "members": project["members"]}
    group["projects"] = [p for p in group.get("projects", []) if p["id"] != project["id"]] + [entry]
    path.write_text(json.dumps(group, indent=2), encoding="utf-8")
