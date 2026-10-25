"""The papers behind an onboarding lesson (backend/onboarding/), read on the researcher's computer.

The researcher gives the paper the code implements: a PDF, its LaTeX source, or a Markdown or
text copy, in a granted folder. read_paper returns its text (a PDF's page by page, which needs
the optional pypdf; LaTeX keeps its equations exactly), and read_code the whole code file, so
the lesson looks at all of it. The project's own citations are found
too (project_references): DOIs and arXiv IDs in the chosen code file (usually in its comments),
in the project's README and CITATION.cff, and the entries of its BibTeX files. Read-only,
through GrantRegistry like the agent's file tools; secrets in what's returned are blanked.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from backend.orchestration.evidence_policy import path_problem, redact_secrets
from local_connector.agent_tools import is_research_file
from local_connector.evidence.extract import file_kind
from local_connector.file_access.grants import AccessDenied, Grant, GrantRegistry
from local_connector.file_access.walk import iter_project_files

MAX_REFERENCES = 20
MAX_BIB_FILES = 5
MAX_CONTEXT = 160
MAX_FIELD = 300
PAPER_SUFFIXES = frozenset({".pdf", ".tex", ".md", ".txt", ".rst"})
MAX_PAPER_CHARS = 30_000  # about 10 pages: pick the pages with the method for a longer paper
MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_PAPERS_LISTED = 200
MAX_CODE_CHARS = 60_000  # a whole source file of about 1,200 lines, with its line numbers
_PAGES = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


class PaperUnreadable(ValueError):
    """The paper's text couldn't be read: no pypdf, a locked or scanned PDF, bad pages."""


def read_code(registry: GrantRegistry, grant: Grant, path: str) -> dict:
    """The whole code file a lesson is about, numbered as read_project_file numbers it (and with the
    same keys), up to MAX_CODE_CHARS; a longer file sends its start and says so (truncated)."""
    problem = path_problem(path)
    if problem:
        raise AccessDenied(f"{path} {problem}.")
    resolved, relative = registry.check_path(path, grant)
    if not resolved.is_file():
        raise AccessDenied(f"There is no {relative} in {grant.name}.")
    if not is_research_file(relative):
        raise AccessDenied(f"{relative} isn't a research file type ExpandRG reads (code, build files, configs, data and logs).")
    with open(resolved, "rb") as f:
        data = f.read(MAX_CODE_CHARS * 2)
        more = bool(f.read(1))
    if b"\x00" in data[:8192]:
        raise AccessDenied(f"{relative} looks like a binary file.")
    lines = data.decode("utf-8", errors="replace").splitlines()
    if more and lines:
        lines.pop()  # cut mid-line at the read limit
    with open(resolved, "rb") as f:
        total = sum(1 for _ in f)
    rows, size = [], 0
    for number, line in enumerate(lines, 1):
        row = f"{number:>5}| {line}"
        if size + len(row) + 1 > MAX_CODE_CHARS:
            break
        rows.append(row)
        size += len(row) + 1
    text, redactions = redact_secrets("\n".join(rows))
    return {"folder": grant.name, "path": relative, "kind": file_kind(relative), "lines": f"1-{len(rows)}" if rows else "none",
            "total_lines": total, "truncated": len(rows) < total, "redactions": redactions, "text": text}


def list_papers(grant: Grant) -> dict:
    """The files in a granted folder that can be a lesson's paper (PDF, LaTeX, Markdown, text)."""
    papers = [relative for relative, _ in iter_project_files(grant, binary_ok=frozenset({".pdf"}))
              if PurePosixPath(relative).suffix.lower() in PAPER_SUFFIXES]
    return {"folder": grant.name, "files": papers[:MAX_PAPERS_LISTED], "total": len(papers)}


def read_paper(registry: GrantRegistry, grant: Grant, path: str, pages: str | None = None) -> dict:
    """{"folder", "path", "kind": "pdf" | "tex" | "text", "pages" (sent, a PDF's), "total_pages", "text",
    "truncated", "redactions"}. A PDF's text is marked "[page N]". Raises AccessDenied for files the
    researcher's rules don't allow and PaperUnreadable when there's no text to read."""
    problem = path_problem(path)
    if problem:
        raise AccessDenied(f"{path} {problem}.")
    resolved, relative = registry.check_path(path, grant)
    if not resolved.is_file():
        raise AccessDenied(f"There is no {relative} in {grant.name}.")
    suffix = PurePosixPath(relative).suffix.lower()
    if suffix not in PAPER_SUFFIXES:
        raise AccessDenied(f"{relative} isn't a paper ExpandRG reads: a PDF, its LaTeX source (.tex), Markdown or plain text.")
    if suffix == ".pdf":
        kind, text, shown, total, cut = "pdf", *_pdf_text(resolved, relative, pages)
    else:
        with open(resolved, "rb") as f:
            data = f.read(MAX_PAPER_CHARS * 4 + 1)
        if b"\x00" in data[:8192]:
            raise PaperUnreadable(f"{relative} looks like a binary file, not text.")
        text = data.decode("utf-8", errors="replace")
        kind, shown, total, cut = ("tex" if suffix == ".tex" else "text"), "", None, len(text) > MAX_PAPER_CHARS
        text = text[:MAX_PAPER_CHARS]
    text, redactions = redact_secrets(text)
    return {"folder": grant.name, "path": relative, "kind": kind, "pages": shown, "total_pages": total,
            "text": text, "truncated": cut, "redactions": redactions}


def _pdf_text(resolved, relative: str, pages: str | None) -> tuple[str, str, int, bool]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise PaperUnreadable('Reading a PDF needs pypdf: pip install "expandrg[papers]" (or pip install pypdf). '
                              "The paper's .tex source, or a text copy, works without it.") from None
    if resolved.stat().st_size > MAX_PDF_BYTES:
        raise PaperUnreadable(f"{relative} is larger than 40 MB. Use its .tex source or a text copy.")
    try:
        reader = PdfReader(str(resolved))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PaperUnreadable(f"{relative} is locked with a password.")
        total = len(reader.pages)
    except PaperUnreadable:
        raise
    except Exception:
        raise PaperUnreadable(f"{relative} couldn't be opened as a PDF.") from None
    first, last = 1, total
    if pages:
        match = _PAGES.match(pages)
        if not match:
            raise PaperUnreadable(f'Bad pages "{pages}". Use "3-7" or "3".')
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        if not 1 <= first <= last or first > total:
            raise PaperUnreadable(f"{relative} has {total} pages.")
        last = min(last, total)
    parts, size, cut, sent = [], 0, False, first - 1
    for number in range(first, last + 1):
        try:
            page = reader.pages[number - 1].extract_text() or ""
        except Exception:
            page = "(this page's text couldn't be read)"
        block = f"[page {number}]\n{page.strip()}\n"
        if size + len(block) > MAX_PAPER_CHARS:
            parts.append(block[: MAX_PAPER_CHARS - size])
            cut = True
            sent = number
            break
        parts.append(block)
        size += len(block)
        sent = number
    text = "".join(parts)
    if not re.search(r"\w{3,}", re.sub(r"\[page \d+\]", "", text)):
        raise PaperUnreadable(f"{relative} has no text to read (a scanned paper?). Use its .tex source or a text copy.")
    return text, f"{first}-{sent}", total, cut or sent < last

DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>{}\[\]|,;]+)")
ARXIV = re.compile(r"(?:\barxiv[:\s]\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", re.I)
BIB_FIELD = re.compile(r"(\w+)\s*=\s*", re.I)


def project_references(registry: GrantRegistry, grant: Grant, path: str) -> dict:
    """{"references": [{"id": "R1", "kind": "doi" | "arxiv" | "bib", "doi", "arxiv", "title", "authors",
    "year", "venue", "file", "line", "context"}], "files": the files searched}. The chosen file comes
    first, then README, CITATION.cff and .bib files; the same paper is listed once."""
    problem = path_problem(path)
    if problem:
        raise AccessDenied(f"{path} {problem}.")
    resolved, chosen = registry.check_path(path, grant)
    if not resolved.is_file():
        raise AccessDenied(f"There is no {chosen} in {grant.name}.")
    others = [relative for relative, _ in iter_project_files(grant) if relative != chosen and _cites(relative)]
    bibs = [f for f in others if f.lower().endswith(".bib")][:MAX_BIB_FILES]
    searched = [chosen] + [f for f in others if not f.lower().endswith(".bib")] + bibs
    found, seen = [], set()
    for relative in searched:
        try:
            _, text, _ = registry.read_file(relative, grant)
        except (AccessDenied, OSError, UnicodeError):
            continue
        entries = _bib_entries(text) if relative.lower().endswith(".bib") else _inline(text)
        for entry in entries:
            key = (entry.get("doi") or "").lower() or entry.get("arxiv") or _plain(entry.get("title", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            found.append({**entry, "file": relative})
            if len(found) >= MAX_REFERENCES:
                break
        if len(found) >= MAX_REFERENCES:
            break
    for i, entry in enumerate(found, 1):
        entry["id"] = f"R{i}"
        for field in ("title", "authors", "venue", "context"):
            if entry.get(field):
                entry[field] = redact_secrets(entry[field])[0]
    return {"folder": grant.name, "path": chosen, "references": found, "files": searched}


def _cites(relative: str) -> bool:
    """README and CITATION files at the top or in docs/, and BibTeX files anywhere."""
    parts = PurePosixPath(relative.lower()).parts
    name = parts[-1]
    if name.endswith(".bib"):
        return True
    near_top = len(parts) == 1 or (len(parts) == 2 and parts[0] in ("doc", "docs"))
    return near_top and (name.startswith("readme") or name in ("citation.cff", "citation.md", "references.md"))


def _inline(text: str) -> list[dict]:
    """DOIs and arXiv IDs, each with its line and the line's text."""
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        context = line.strip()[:MAX_CONTEXT]
        for match in DOI.finditer(line):
            entries.append({"kind": "doi", "doi": match.group(1).rstrip(".)"), "line": number, "context": context})
        for match in ARXIV.finditer(line):
            entries.append({"kind": "arxiv", "arxiv": match.group(1), "line": number, "context": context})
    return entries


def _bib_entries(text: str) -> list[dict]:
    """Each BibTeX entry's title, authors, year, venue and DOI (braces and line breaks removed)."""
    entries = []
    for match in BIB_ENTRY.finditer(text):
        if match.group(1).lower() in ("comment", "string", "preamble"):
            continue
        fields, at = {}, match.end()
        while True:
            field = BIB_FIELD.match(text, _skip(text, at))
            if not field:
                break
            value, at = _value(text, field.end())
            fields[field.group(1).lower()] = value
            at = _skip(text, at)
            if at < len(text) and text[at] == ",":
                at += 1
            else:
                break
        title = fields.get("title", "")
        if not title and not fields.get("doi"):
            continue
        year = re.search(r"\d{4}", fields.get("year", ""))
        doi = DOI.search(fields.get("doi", ""))
        entries.append({"kind": "bib", "key": match.group(2)[:80], "title": title[:MAX_FIELD],
                        "authors": fields.get("author", "")[:MAX_FIELD], "year": int(year.group()) if year else None,
                        "venue": (fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or "")[:MAX_FIELD],
                        **({"doi": doi.group(1)} if doi else {}), "line": text.count("\n", 0, match.start()) + 1})
    return entries


def _skip(text: str, at: int) -> int:
    while at < len(text) and text[at].isspace():
        at += 1
    return at


def _value(text: str, at: int) -> tuple[str, int]:
    """A field value: {braced, possibly {nested}}, "quoted", or a bare word or number."""
    if at < len(text) and text[at] in "{\"":
        close, depth, start = "}" if text[at] == "{" else "\"", 0, at + 1
        at += 1
        while at < len(text):
            char = text[at]
            if char == "{":
                depth += 1
            elif char == "}" and depth:
                depth -= 1
            elif char == close and not depth:
                return _clean(text[start:at]), at + 1
            at += 1
        return _clean(text[start:]), at
    word = re.match(r"[^,}\s]*", text[at:]).group()
    return _clean(word), at + len(word)


def _clean(value: str) -> str:
    return " ".join(value.replace("{", "").replace("}", "").split())


def _plain(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
