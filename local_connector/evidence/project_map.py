"""A map of a granted project folder, so a researcher's agent can understand the
project as a whole without the whole repo ever leaving the computer.

The map holds:
- the folder tree (file names and sizes; big folders are summarized by file type);
- the start of the key files: the README, dependency and build files, configs,
  an outline of the main scripts (opening lines plus function and class names)
  and the headings of notebooks.
Sensitive and binary files are never included, secrets are blanked, and the
whole map stays within a size limit. The researcher previews it before sending.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from backend.orchestration.evidence_policy import redact_secrets
from local_connector.evidence.extract import _numbered, file_kind
from local_connector.file_access.grants import AccessDenied, Grant, GrantRegistry
from local_connector.file_access.walk import WalkStats, iter_project_files

MAX_TREE_CHARS = 6_000
MAX_MAP_EXCERPTS = 16
MAX_MAP_EXCERPT_CHARS = 1_800
MAX_MAP_CHARS = 24_000  # tree + excerpts
MAX_LISTED_PATHS = 2_000  # file list kept for checking the agent's answer; never sent to CREAO
MAX_CONFIG_BYTES = 200_000
DIR_LISTING_LIMIT = 8  # more files than this in one folder are summarized by type
TREE_DEPTH = 4

README = re.compile(r"^readme(\.\w+)?$", re.IGNORECASE)
DEPENDENCY_FILES = {
    "requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml", "setup.py", "setup.cfg",
    "pipfile", "cmakelists.txt", "makefile", "package.json", "cargo.toml", "project.toml", "description",
    "meson.build", "configure.ac",
}
NOT_CONFIG = {"package-lock.json", "yarn.lock", "poetry.lock", "pipfile.lock", "manifest.toml"}
CODE_SUFFIXES = {".py", ".m", ".jl", ".f", ".f90", ".f95", ".f03", ".f08", ".c", ".cc", ".cpp", ".cxx", ".h",
                 ".hpp", ".cu", ".r", ".rs", ".go", ".java", ".js", ".ts", ".sh"}
# Name parts that suggest a main script or a config, matched as whole parts ("cli" not in "client").
ENTRY_WORDS = {"main", "run", "app", "cli", "sim", "model", "driver", "solver", "solve", "train", "simulate",
               "simulation", "experiment", "pipeline"}
CONFIG_WORDS = {"config", "configs", "params", "parameters", "settings", "input", "inputs", "case", "setup"}
TEST_FOLDERS = {"test", "tests", "testing", "fixtures", "__tests__"}
FORTRAN = {".f", ".f90", ".f95", ".f03", ".f08"}
C_LIKE = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cu", ".java", ".go", ".rs", ".js", ".ts"}
SIGNATURES = {
    ".py": re.compile(r"^\s*(async\s+def|def|class)\s+\w+"),
    ".jl": re.compile(r"^\s*(function|module|struct|mutable\s+struct|macro)\b"),
    ".m": re.compile(r"^\s*function\b"),
    ".r": re.compile(r"^\s*[\w.]+\s*(<-|=)\s*function\b"),
    ".sh": re.compile(r"^\s*(\w+\s*\(\)\s*\{|function\s+\w+)"),
}
FORTRAN_SIGNATURE = re.compile(r"^\s*(program|module|subroutine|contains|(\w+(\(\w+\))?\s+)*function)\b", re.I)
C_SIGNATURE = re.compile(r"^(class|struct|namespace|fn|pub\s+fn|func|function|export|def)\b|^[A-Za-z_][\w:<>,*&\s]*\([^;]*\)\s*(const\s*)?\{?\s*$")
HEAD_LINES = {"readme": 60, "dependencies": 40, "config": 40}
OUTLINE_HEAD_LINES = 12
OUTLINE_SIGNATURES = 40


@dataclass
class MapItem:
    path: str
    kind: str  # "readme", "dependencies", "config", "code outline" or "notebook outline"
    excerpt: str
    truncated: bool


@dataclass
class ProjectMap:
    project: str
    tree: str
    files: list[str]
    items: list[MapItem] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    redactions: int = 0
    left_out: list[str] = field(default_factory=list)  # key files that didn't fit the size limit

    @property
    def total_chars(self) -> int:
        return len(self.tree) + sum(len(i.excerpt) for i in self.items)

    def to_payload(self) -> dict:
        return {
            "tree": self.tree,
            "files": self.files,
            "excerpts": [{"path": i.path, "kind": i.kind, "excerpt": i.excerpt, "truncated": i.truncated}
                         for i in self.items],
            "stats": self.stats,
        }


def build_project_map(registry: GrantRegistry, grant: Grant) -> ProjectMap:
    walk = WalkStats()
    entries = [(relative, full.stat().st_size) for relative, full in iter_project_files(grant, walk)]
    tree, tree_redactions = redact_secrets(_render_tree(grant.name, entries))
    if len(tree) > MAX_TREE_CHARS:
        tree = tree[: MAX_TREE_CHARS - 30].rstrip() + "\n  [... tree shortened]"
    by_suffix = Counter(PurePosixPath(p).suffix.lower() or "(none)" for p, _ in entries)
    project_map = ProjectMap(
        project=grant.name,
        tree=tree,
        files=[p for p, _ in entries][:MAX_LISTED_PATHS],
        stats={"files": len(entries), "by_type": dict(by_suffix.most_common(12)), "left_out_by_rule": dict(walk.skipped)},
        redactions=tree_redactions,
    )

    for path, kind in _key_files(entries):
        if len(project_map.items) >= MAX_MAP_EXCERPTS:
            project_map.left_out.append(path)
            continue
        budget = min(MAX_MAP_EXCERPT_CHARS, MAX_MAP_CHARS - project_map.total_chars)
        if budget < 300:
            project_map.left_out.append(path)
            continue
        try:
            relative, text, read_truncated = registry.read_file(path, grant)
        except AccessDenied:
            continue
        excerpt, kept_all = _excerpt(relative, text, kind)
        excerpt, redacted = redact_secrets(excerpt)
        project_map.redactions += redacted
        truncated = read_truncated or not kept_all
        if len(excerpt) > budget:
            excerpt, truncated = excerpt[: budget - 20].rstrip() + "\n  [... shortened]", True
        project_map.items.append(MapItem(relative, kind, excerpt, truncated))
    return project_map


def _key_files(entries: list[tuple[str, int]]) -> list[tuple[str, str]]:
    """The files worth an excerpt, most useful first."""
    def depth(path: str) -> int:
        return path.count("/")

    def name(path: str) -> str:
        return PurePosixPath(path).name.lower()

    def words(path: str) -> set[str]:
        return set(re.split(r"[^a-z0-9]+", PurePosixPath(path).stem.lower()))

    def is_test(path: str) -> bool:
        parts = [p.lower() for p in PurePosixPath(path).parts]
        return any(p in TEST_FOLDERS for p in parts[:-1]) or parts[-1].startswith("test_")

    paths = [p for p, size in entries]
    # Tests and empty files stay in the tree but don't get excerpts.
    main_paths = [p for p, size in entries if size > 0 and not is_test(p)]
    sizes = dict(entries)
    chosen: list[tuple[str, str]] = []
    readmes = sorted((p for p in main_paths if README.match(name(p)) and depth(p) <= 1), key=depth)
    chosen += [(p, "readme") for p in readmes[:3]]
    chosen += [(p, "dependencies") for p in main_paths if name(p) in DEPENDENCY_FILES and depth(p) <= 1][:4]
    configs = [p for p in main_paths if file_kind(p) == "config" and depth(p) <= 2 and name(p) not in NOT_CONFIG
               and name(p) not in DEPENDENCY_FILES and sizes[p] <= MAX_CONFIG_BYTES]
    configs.sort(key=lambda p: (not words(p) & CONFIG_WORDS, depth(p), p))
    chosen += [(p, "config") for p in configs[:4]]
    code = [p for p in main_paths if PurePosixPath(p).suffix.lower() in CODE_SUFFIXES and depth(p) <= 2]
    code.sort(key=lambda p: (not words(p) & ENTRY_WORDS, depth(p), p))
    chosen += [(p, "code outline") for p in code[:6]]
    notebooks = sorted((p for p in main_paths if p.lower().endswith(".ipynb") and depth(p) <= 2), key=depth)
    chosen += [(p, "notebook outline") for p in notebooks[:2]]
    return chosen


def _excerpt(path: str, text: str, kind: str) -> tuple[str, bool]:
    lines = text.splitlines()
    if kind == "notebook outline":
        return _notebook_outline(text), False
    if kind == "code outline":
        suffix = PurePosixPath(path).suffix.lower()
        pattern = SIGNATURES.get(suffix) or (FORTRAN_SIGNATURE if suffix in FORTRAN else C_SIGNATURE)
        hits = [n for n, line in enumerate(lines, start=1) if n > OUTLINE_HEAD_LINES and pattern.search(line)]
        ranges = [(1, min(OUTLINE_HEAD_LINES, len(lines)))] + [(n, n) for n in hits[:OUTLINE_SIGNATURES]]
        return _numbered(lines, ranges)
    head = HEAD_LINES.get(kind, 40)
    return _numbered(lines, [(1, min(head, len(lines)))])


def _notebook_outline(text: str) -> str:
    try:
        cells = json.loads(text).get("cells", [])
    except (ValueError, AttributeError):
        return "(could not read this notebook)"
    out, code_cells = [], 0
    for cell in cells:
        source = "".join(cell.get("source", [])) if isinstance(cell.get("source"), list) else str(cell.get("source", ""))
        first = next((l for l in source.splitlines() if l.strip()), "")
        if cell.get("cell_type") == "markdown" and first:
            out.append(f"[markdown] {first[:150]}")
        elif cell.get("cell_type") == "code":
            code_cells += 1
            if first and len(out) < 40:
                out.append(f"[code] {first[:150]}")
        if len(out) >= 40:
            break
    out.append(f"({len(cells)} cells, {code_cells} code cells)")
    return "\n".join(out)


def _render_tree(project: str, entries: list[tuple[str, int]]) -> str:
    children: dict[str, set[str]] = defaultdict(set)
    files_in: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for path, size in entries:
        parts = path.split("/")
        for i in range(len(parts) - 1):
            children["/".join(parts[:i])].add("/".join(parts[: i + 1]))
        files_in["/".join(parts[:-1])].append((parts[-1], size))

    def count(folder: str) -> int:
        return len(files_in.get(folder, [])) + sum(count(c) for c in children.get(folder, ()))

    lines = [f"{project}/  ({len(entries)} files)"]

    def walk(folder: str, level: int) -> None:
        indent = "  " * level
        for sub in sorted(children.get(folder, ())):
            name = sub.rsplit("/", 1)[-1]
            if level >= TREE_DEPTH:
                lines.append(f"{indent}{name}/  ({count(sub)} files)")
                continue
            lines.append(f"{indent}{name}/  ({count(sub)} files)")
            walk(sub, level + 1)
        files = sorted(files_in.get(folder, []))
        if len(files) > DIR_LISTING_LIMIT:
            types = Counter(PurePosixPath(n).suffix.lower() or "(none)" for n, _ in files)
            summary = ", ".join(f"{t} x{c}" for t, c in types.most_common(5))
            shown = ", ".join(n for n, _ in files[:3])
            lines.append(f"{indent}{len(files)} files: {summary} (e.g. {shown}, ...)")
        else:
            lines.extend(f"{indent}{n}  ({_size(s)})" for n, s in files)

    walk("", 1)
    return "\n".join(lines)


def _size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"
