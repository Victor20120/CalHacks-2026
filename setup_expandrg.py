"""Set up a research group in ExpandRG using agents already created in CREAO.

    python setup_expandrg.py

Asks, in this terminal: whether you're the group leader; then the group's name, the Super
Brain's CREAO app ID and key, each researcher's name and their agent's app ID and key, and
the first project. Keys are typed hidden, checked with CREAO (no credits) and saved only on
this computer. The demo group stays as it is. Start the new group afterwards with:

    python run_demo.py --group <id>
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.workspaces.groups import (  # noqa: E402  (needs the repo on the path)
    Agent,
    GroupError,
    GroupSpec,
    Person,
    app_id_problem,
    check_app,
    check_key,
    create_group,
    groups_home,
    key_problem,
    slugify,
)

WHERE = """  Find the app ID and key for the agent you've already created:
    1. Go to agent.creao.ai and open the agent.
    2. On the agent's page, open its API Trigger settings.
    3. Copy the app ID: 36 characters, like 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f.
    4. Copy an API key that starts with capi_ (create one there if there isn't one yet).
  Paste the app ID below. The key is entered next, with typing hidden."""

APP_RESULTS = {"ok": "found", "not_found": "NOT FOUND: check this app ID", "unreachable": "couldn't check (no answer from CREAO)"}


def run(ask=input, ask_secret=getpass.getpass, say=print, key_checker=check_key, app_checker=check_app) -> int:
    say("ExpandRG setup")
    say("Connect the Super Brain and personal agents you've already created in CREAO.\n")
    while True:
        role = ask("Are you the group leader? (y/n): ").strip().lower()
        if role in ("y", "yes", "n", "no"):
            break
        say("  Answer y or n.")
    if role in ("n", "no"):
        say("\nOnly the group leader sets up a group and connects everyone's agents.")
        say("Ask your leader to run: python setup_expandrg.py")
        say("Wait until your leader gives you the group ID and your sign-in name/member ID.")
        say("There is nothing to enter or create here yet.")
        return 0

    say(f"\nYour group's keys and empty research memory will be saved on this computer in {groups_home()}.")
    while True:
        group_name = _required(ask, say, "Group name: ")
        if not slugify(group_name):
            say("  Use letters or digits in the name.")
        elif (groups_home() / slugify(group_name)).exists():
            say(f'  A group called "{slugify(group_name)}" already exists on this computer. Choose another name.')
        else:
            break
    leader = Person(_required(ask, say, "Your name: "))

    say("\nStep 1 of 3 · The Super Brain: your group's shared CREAO agent")
    say(WHERE)
    super_brain = _agent(ask, ask_secret, say, key_checker, "Super Brain")

    say("\nStep 2 of 3 · The researchers and their personal agents")
    say("  Each researcher uses their own existing CREAO agent.")
    say("  Open that agent > API Trigger > copy its app ID and capi_ key.")
    researchers: list[Person] = []
    while True:
        name = ask(f"Researcher {len(researchers) + 1} name (press Enter when you've added everyone): ").strip()
        if not name:
            if researchers:
                break
            say("  Add at least one researcher.")
            continue
        researchers.append(Person(name, _agent(ask, ask_secret, say, key_checker, f"{name}'s agent")))

    say("\nStep 3 of 3 · The first project")
    while True:
        project = _required(ask, say, "Project name: ")
        if len(slugify(project)) >= 2:
            break
        say("  Use at least two letters or digits.")
    members = _members(ask, say, researchers)

    say("\nCheck before saving:")
    say(f"  Group: {group_name}, led by {leader.name}")
    say(f"  Super Brain: app {super_brain.app_id}, key {_masked(super_brain.key)}")
    for r in researchers:
        say(f"  {r.name}: app {r.agent.app_id}, key {_masked(r.agent.key)}")
    on_project = [researchers[i].name for i in members] if members else [r.name for r in researchers]
    say(f"  Project: {project}, with {leader.name} and {', '.join(on_project)}")

    if _yes(ask("\nTest each app ID with one short CREAO run? It uses a little credit. (y/N): ")):
        for label, agent in [("Super Brain", super_brain), *((r.name, r.agent) for r in researchers)]:
            result = app_checker(agent)
            while result == "not_found":
                say(f"  {label}: NOT FOUND. Paste this agent's app ID again.")
                agent.app_id = _app_id(ask, say, f"{label}: app ID")
                result = app_checker(agent)
            say(f"  {label}: {APP_RESULTS[result]}")

    if not _yes(ask("\nSave this group? (y/n): ")):
        say("Nothing was saved.")
        return 1
    spec = GroupSpec(group_name, leader, super_brain, researchers, project, members)
    try:
        folder = create_group(spec)
    except GroupError as exc:
        say(str(exc))
        return 1

    group_id = folder.name
    project_id = slugify(project)
    say(f"\nSaved in {folder}")
    say("\nStart your group (the demo can't run at the same time):")
    say(f"  python run_demo.py --group {group_id}")
    say("\nThen sign in on the dashboard as:")
    say(f"  {leader.name} (leader)")
    for r in researchers:
        say(f"  {r.name}")
    say("\nIn VS Code, set these in your workspace settings (.vscode/settings.json):")
    say(f'  "expandrg.group": "{group_id}",')
    say(f'  "expandrg.project": "{project_id}",')
    say(f'  "expandrg.member": one of {", ".join(repr(p.id) for p in [leader, *researchers])}')
    return 0


def _agent(ask, ask_secret, say, key_checker, label: str) -> Agent:
    app_id = _app_id(ask, say, f"{label}: app ID")
    while True:
        key = ask_secret(f"  {label}: key (typing is hidden): ").strip()
        problem = key_problem(key)
        if problem:
            say(f"  {problem}")
            continue
        result = key_checker(Agent(app_id, key))
        if result == "rejected":
            say("  CREAO refused this key. Copy it again, all of it.")
            continue
        say("  Couldn't reach CREAO to check the key. It's kept; check your internet connection."
            if result == "unreachable" else "  OK: CREAO accepted the key.")
        return Agent(app_id, key)


def _app_id(ask, say, prompt: str) -> str:
    while True:
        app_id = ask(f"  {prompt}: ").strip()
        problem = app_id_problem(app_id)
        if not problem:
            return app_id
        say(f"  {problem}")


def _members(ask, say, researchers: list[Person]) -> list[int]:
    """Indexes of the researchers on the first project; empty means everyone."""
    if len(researchers) == 1:
        return []
    listed = ", ".join(f"{n}. {r.name}" for n, r in enumerate(researchers, 1))
    while True:
        answer = ask(f"Who works on it? {listed}. Type numbers like 1,3, or press Enter for everyone: ").strip()
        if not answer:
            return []
        try:
            picked = sorted({int(part) - 1 for part in answer.replace(" ", "").split(",") if part})
        except ValueError:
            picked = [-1]
        if picked and all(0 <= i < len(researchers) for i in picked):
            return picked
        say(f"  Use numbers from 1 to {len(researchers)}, separated by commas.")


def _required(ask, say, prompt: str) -> str:
    while True:
        value = ask(prompt).strip()
        if value:
            return value
        say("  This one is needed.")


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes")


def _masked(key: str) -> str:
    return f"capi_****{key[-4:]}"


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Nothing was saved.")
        raise SystemExit(1)
