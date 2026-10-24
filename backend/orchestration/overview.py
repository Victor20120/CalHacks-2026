"""Project overview ("What is this project?"): the researcher's agent explains a
project from a map of it that the researcher approved, and the explanation is
saved privately as that project's context for every later diagnosis.

As with diagnoses, the backend re-checks the map from the local connector
instead of trusting it, and treats the agent's answer as untrusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.creao.client import extract_json_block
from backend.orchestration.diagnosis import EvidenceRejected
from backend.orchestration.evidence_policy import path_problem, redact_secrets

MAX_TREE_CHARS = 8_000
MAX_OVERVIEW_ITEMS = 20
MAX_OVERVIEW_ITEM_CHARS = 2_500
MAX_OVERVIEW_TOTAL_CHARS = 26_000
MAX_LISTED_PATHS = 2_000
MAX_CONTEXT_CHARS = 2_500  # how much of a saved overview goes into each later diagnosis

OVERVIEW_INSTRUCTIONS = (
    "After your normal reply, end with one fenced ```json code block and nothing after it. "
    'The block must be a JSON object of the form {"overview": {"purpose": "...", '
    '"structure": [{"path": "...", "role": "..."}], "how_to_run": "...", '
    '"key_parameters": [{"name": "...", "value": "...", "meaning": "..."}], "open_questions": ["..."]}}. '
    "Use only paths that appear in the project map. Put no other text inside the block."
)
ONBOARDING_REQUEST = (
    "No problem to diagnose yet. A researcher is starting work on this project and needs to understand it. "
    "Using only the project map below (the folder tree and the start of key files, selected and approved by "
    "the researcher), explain: what the project does, how it is organized, what each important file or folder "
    "is for, how to run it, the key parameters and settings, and what is unclear or missing. Do not invent "
    "files that are not in the map, and say when you are guessing."
)
# A tree line ends in "(<size>)" or "(<n> files)"; the name is what's before it.
_TREE_NAME = re.compile(r"^\s*(.+?)/?\s+\((?:[\d.]+ (?:B|KB|MB|GB)|\d+ files)\)\s*$")


@dataclass
class PreparedOverview:
    project: str
    tree: str
    excerpts: list[dict[str, Any]]
    files: list[str]
    stats: dict
    redactions: int
    agent_rules: str = ""

    def creao_inputs(self) -> dict[str, str]:
        parts = [ONBOARDING_REQUEST, f"Folder tree:\n{self.tree}"]
        for item in self.excerpts:
            note = ", partial" if item["truncated"] else ""
            parts.append(f"--- {item['path']} ({item['kind']}{note}) ---\n{item['excerpt']}")
        inputs = {
            "project_path": f"{self.project} (local files are not attached; a map of the project is in observed_symptom)",
            "observed_symptom": "\n\n".join(parts),
            "focus_areas": "all",
            "analysis_depth": "standard",
            "output_format": OVERVIEW_INSTRUCTIONS,
        }
        if self.agent_rules:
            from backend.orchestration.chat import with_agent_rules
            inputs = with_agent_rules(inputs, self.agent_rules)
        return inputs


def prepare_overview(project: str, tree: str, excerpts: list[dict[str, Any]], files: list[str], stats: dict) -> PreparedOverview:
    """Check a project map. Raises EvidenceRejected; nothing is sent if it does."""
    if len(tree) > MAX_TREE_CHARS:
        raise EvidenceRejected(f"The folder tree is over {MAX_TREE_CHARS} characters.", 413)
    if len(excerpts) > MAX_OVERVIEW_ITEMS:
        raise EvidenceRejected(f"Send at most {MAX_OVERVIEW_ITEMS} file excerpts.", 413)
    if len(files) > MAX_LISTED_PATHS:
        raise EvidenceRejected(f"List at most {MAX_LISTED_PATHS} files.", 413)
    for line in tree.splitlines()[1:]:
        match = _TREE_NAME.match(line)
        if match and path_problem(match.group(1).strip()):
            raise EvidenceRejected(f"The folder tree names a file that is never sent: {match.group(1).strip()}")
    for path in files:
        problem = path_problem(path)
        if problem:
            raise EvidenceRejected(f"Listed path '{path}' {problem}.")

    redactions = 0
    tree, n = redact_secrets(tree)
    redactions += n
    checked = []
    for item in excerpts:
        problem = path_problem(item["path"])
        if problem:
            raise EvidenceRejected(f"Excerpt path '{item['path']}' {problem}.")
        if len(item["excerpt"]) > MAX_OVERVIEW_ITEM_CHARS:
            raise EvidenceRejected(f"Excerpt '{item['path']}' is over {MAX_OVERVIEW_ITEM_CHARS} characters.", 413)
        excerpt, n = redact_secrets(item["excerpt"])
        redactions += n
        checked.append({**item, "excerpt": excerpt})
    if len(tree) + sum(len(i["excerpt"]) for i in checked) > MAX_OVERVIEW_TOTAL_CHARS:
        raise EvidenceRejected(f"The project map is over {MAX_OVERVIEW_TOTAL_CHARS} characters in total.", 413)
    return PreparedOverview(project.strip(), tree, checked, list(files), dict(stats), redactions)


def run_overview(client: Any, prepared: PreparedOverview) -> dict[str, Any]:
    """Ask the researcher's agent for the overview and check its answer."""
    result = client.run(prepared.creao_inputs())
    reply, data = extract_json_block(result.reply, "overview")
    return {
        "run_id": result.run_id,
        "reply": reply,
        "overview": clean_overview(data, prepared.files, root_name(prepared.tree)),
    }


def clean_overview(data: Any, known_paths: list[str], root: str = "") -> dict | None:
    """Keep only well-formed fields from the agent's overview. Every path is marked
    with whether it really exists in the project, so invented files show up."""
    if not isinstance(data, dict):
        return None
    folders = {p.rsplit("/", i)[0] for p in known_paths for i in range(1, p.count("/") + 1)}
    known = set(known_paths) | folders | ({root, "."} if root else {"."})  # the project folder itself counts

    def text(value: Any, limit: int) -> str:
        return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""

    structure = []
    for entry in data.get("structure") or []:
        if isinstance(entry, dict) and text(entry.get("path"), 300):
            path = text(entry.get("path"), 300)
            structure.append({"path": path, "role": text(entry.get("role"), 400),
                              "in_project": path.rstrip("/") in known})
    parameters = [
        {"name": text(p.get("name"), 120), "value": text(p.get("value"), 120), "meaning": text(p.get("meaning"), 300)}
        for p in data.get("key_parameters") or [] if isinstance(p, dict) and text(p.get("name"), 120)
    ]
    questions = [text(q, 400) for q in data.get("open_questions") or [] if text(q, 400)]
    return {
        "purpose": text(data.get("purpose"), 2000),
        "structure": structure[:40],
        "how_to_run": text(data.get("how_to_run"), 2000),
        "key_parameters": parameters[:30],
        "open_questions": questions[:15],
    }


def context_text(context: dict | None) -> str:
    """A short version of a saved overview, for later diagnoses on the same project."""
    if not context:
        return ""
    overview = context.get("overview") or {}
    parts = []
    if overview.get("purpose"):
        parts.append(f"Purpose: {overview['purpose']}")
    if overview.get("structure"):
        parts.append("Key files: " + "; ".join(
            f"{s['path']} — {s['role']}" for s in overview["structure"][:15] if s.get("in_project")))
    if overview.get("how_to_run"):
        parts.append(f"How to run: {overview['how_to_run']}")
    if overview.get("key_parameters"):
        parts.append("Key parameters: " + "; ".join(
            f"{p['name']} = {p['value']}" if p.get("value") else p["name"] for p in overview["key_parameters"][:12]))
    if not parts and context.get("reply"):
        parts.append(context["reply"])
    text = "\n".join(parts)
    return text if len(text) <= MAX_CONTEXT_CHARS else text[: MAX_CONTEXT_CHARS - 6].rstrip() + " [...]"


def root_name(tree: str) -> str:
    """The project folder's name: the first line of the tree, e.g. "my_project/  (40 files)"."""
    first = tree.splitlines()[0] if tree else ""
    return first.split("/")[0].strip()
