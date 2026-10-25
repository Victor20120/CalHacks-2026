"""Checking a lesson's papers on Crossref (https://api.crossref.org), the public index of DOIs.

The Super Brain names papers from memory, and a name can be wrong or made up. So each paper
in a lesson gets a status the researcher can trust:
- "provided": the paper the researcher gave for the lesson (a DOI link is added when Crossref
  has its title);
- "project": the project cites it itself (a DOI, arXiv ID or BibTeX entry, with its file and line);
- "crossref": Crossref has a work with that DOI and title, or one whose title, first author and
  year match closely; the lesson links its DOI;
- "doi_mismatch": the DOI given belongs to a different paper (its real title is in the note);
- "not_found": Crossref has nothing close; it may be wrong;
- "unchecked": Crossref couldn't be reached.

Only bibliographic details leave this computer: a DOI, or a title with its first author and
year. Never code, the question, or the group's findings.
"""
from __future__ import annotations

import difflib
import re
import time
from typing import Any
from urllib.parse import quote

import requests

CROSSREF = "https://api.crossref.org/works"
TIMEOUT = 8
ROWS = 5
ATTEMPTS = 4  # Crossref answers 429 when asked too fast: wait as it says, then ask again
SPACING = 0.5  # seconds between requests
MAX_WAIT = 8
TITLE_MATCH = 0.85  # how alike two titles must be to count as the same paper
SURE_MATCH = 0.95  # enough without the first author matching
DOI_TITLE = 0.6  # a DOI's own title against the title the lesson gives it
HEADERS = {"User-Agent": "ExpandRG (research group assistant; checks the papers in onboarding lessons)"}


class LookupFailed(Exception):
    """Crossref couldn't be reached, or answered with an error."""


class CrossrefLookup:
    def __init__(self, http: Any = None, sleep: Any = time.sleep):
        self.http, self.sleep, self.last = http or requests.Session(), sleep, 0.0

    def by_doi(self, doi: str) -> dict | None:
        """The work with this DOI, or None when Crossref has none."""
        response = self._get(f"{CROSSREF}/{quote(doi, safe='/')}")
        if response.status_code == 404:
            return None
        return work(response.json().get("message") or {})

    def search(self, title: str, author: str = "", year: int | None = None) -> list[dict]:
        query = " ".join(str(p) for p in (title, author, year or "") if p)
        response = self._get(CROSSREF, {"query.bibliographic": query[:300], "rows": ROWS,
                                        "select": "DOI,title,author,issued,container-title,type"})
        return [work(item) for item in (response.json().get("message") or {}).get("items", [])]

    def _get(self, url: str, params: dict | None = None) -> Any:
        """One request, spaced from the last; a 429 or 5xx is asked again after Crossref's Retry-After
        (or a growing pause). Raises LookupFailed when it still fails, or Crossref can't be reached."""
        for attempt in range(1, ATTEMPTS + 1):
            wait = SPACING - (time.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = time.monotonic()
            try:
                response = self.http.get(url, headers=HEADERS, timeout=TIMEOUT, **({"params": params} if params else {}))
            except requests.RequestException as exc:
                raise LookupFailed(str(exc)) from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == ATTEMPTS:
                    raise LookupFailed(f"HTTP {response.status_code}")
                retry = str((getattr(response, "headers", None) or {}).get("Retry-After", ""))
                self.sleep(min(MAX_WAIT, float(retry) if retry.isdigit() else 1.5 * attempt))
                continue
            if response.status_code >= 400 and response.status_code != 404:
                raise LookupFailed(f"HTTP {response.status_code}")
            return response
        raise LookupFailed("no answer")


def work(item: dict) -> dict:
    """The parts of a Crossref record a lesson shows (titles can carry markup such as <i>, removed)."""
    parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    return {"doi": item.get("DOI", ""), "title": " ".join(re.sub(r"<[^>]+>", "", (item.get("title") or [""])[0]).split()),
            "authors": ", ".join(" ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name", "")
                                 for a in (item.get("author") or [])[:6]),
            "families": [(a.get("family") or a.get("name") or "").lower() for a in item.get("author") or []],
            "year": parts[0] if parts and isinstance(parts[0], int) else None,
            "venue": " ".join((item.get("container-title") or [""])[0].split()), "type": item.get("type", "")}


def check_sources(sources: list[dict], references: list[dict], lookup: Any) -> list[dict]:
    """Each source with "status", "note", and when found "doi", "url" and "record" (Crossref's own title,
    authors, year and venue). references are the project's own (local_connector/references.py)."""
    cited = {r["id"]: r for r in references}
    checked, reachable = [], True
    for source in sources:
        out = {**source}
        ref = cited.get(source.get("ref") or "")
        try:
            if source.get("provided"):
                out.update(status="provided", note=f"You gave this paper: {source['file']}.")
                if source.get("title") and reachable:
                    found = _search(source, lookup)
                    if found["status"] == "crossref":
                        out.update({k: found[k] for k in ("doi", "url", "record")})
            elif ref:
                out.update(status="project", note=f"Cited in the project: {ref['file']}, line {ref['line']}.")
                doi = ref.get("doi") or source.get("doi")
                if ref.get("arxiv"):
                    out.setdefault("url", f"https://arxiv.org/abs/{ref['arxiv']}")
                if doi and reachable:
                    found = lookup.by_doi(doi)
                    if found:
                        out.update(doi=found["doi"], url=f"https://doi.org/{found['doi']}", record=_record(found))
            elif not reachable:
                out.update(status="unchecked", note="Crossref couldn't be reached, so this wasn't checked.")
            else:
                out.update(_on_crossref(source, lookup))
        except LookupFailed:
            reachable = False
            if out.get("status") not in ("project", "provided"):
                out.update(status="unchecked", note="Crossref couldn't be reached, so this wasn't checked.")
        checked.append(out)
    return checked


def _on_crossref(source: dict, lookup: Any) -> dict:
    title, doi = source.get("title", ""), source.get("doi", "")
    if doi:
        found = lookup.by_doi(doi)
        if found and (not title or likeness(found["title"], title) >= DOI_TITLE):
            return _found(found, source)
        if found:
            mismatch = {"status": "doi_mismatch", "doi": "",
                        "note": f"The DOI given, {doi}, belongs to a different work: “{found['title']}”."}
            if not title:
                return mismatch
            searched = _search(source, lookup)
            return searched if searched.get("status") == "crossref" else mismatch
    if not title:
        return {"status": "not_found", "note": "No title or DOI to look up."}
    return _search(source, lookup)


def _search(source: dict, lookup: Any) -> dict:
    title, year = source.get("title", ""), source.get("year")
    first = _first_family(source.get("authors", ""))
    best, best_score = None, 0.0
    for candidate in lookup.search(title, first, year):
        score = likeness(candidate["title"], title)
        if first and first not in candidate["families"]:
            score = score if score >= SURE_MATCH else 0.0  # a different first author needs a near-identical title
        if score > best_score:
            best, best_score = candidate, score
    if best is None or best_score < TITLE_MATCH:
        return {"status": "not_found", "doi": "",
                "note": "Crossref has no paper with this title and first author. It may be wrong, or not indexed there."}
    return _found(best, source)


def _found(found: dict, source: dict) -> dict:
    note = "Found on Crossref."
    year = source.get("year")
    if year and found["year"] and abs(found["year"] - year) > 2:
        note = f"Found on Crossref, dated {found['year']} (the lesson says {year}: a reprint or another edition?)."
    return {"status": "crossref", "doi": found["doi"], "url": f"https://doi.org/{found['doi']}", "record": _record(found),
            "note": note}


def _record(found: dict) -> dict:
    return {k: found[k] for k in ("title", "authors", "year", "venue", "type")}


def likeness(a: str, b: str) -> float:
    """How alike two titles are, from 0 to 1, ignoring case and punctuation. A title that is the
    other without its subtitle counts as alike."""
    a, b = _plain(a), _plain(b)
    if not a or not b:
        return 0.0
    short, long = sorted((a, b), key=len)
    if len(short) >= 25 and long.startswith(short):
        return 0.95
    return difflib.SequenceMatcher(None, a, b).ratio()


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _first_family(authors: str) -> str:
    """The first author's family name, from "J. Crank and P. Nicolson" or "Crank, J.; Nicolson, P."."""
    first = re.split(r"\s+and\s+|;|&", authors.strip(), maxsplit=1)[0].strip()
    if "," in first:
        return first.split(",")[0].strip().lower()
    words = [w for w in re.split(r"\s+", first) if w and not w.endswith(".")]
    return words[-1].lower() if words else ""
