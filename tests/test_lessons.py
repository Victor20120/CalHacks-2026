"""Onboarding lessons: give the Super Brain the paper and the code, and it writes the lesson: words,
equations, pictures, with its papers checked.

The local connector is the real one, on a temporary copy of the sample heat project. CREAO and
Crossref are faked: nothing here uses credits or the network.
"""
import json
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import ChatSettings
from backend.db.database import Database
from backend.onboarding.lessons import Code, LessonUnreadable, lesson_block, parse_lesson
from backend.onboarding.papers import CrossrefLookup, LookupFailed, check_sources, likeness
from backend.onboarding.pictures import FormulaRefused, check as check_pictures, compile_formula
from backend.orchestration.toolkit import ConnectorClient
from local_connector.file_access.grants import GrantRegistry
from local_connector.service import create_service

SAMPLE = Path(__file__).parent / "fixtures" / "sample_project"
CN_DOI = "10.1017/S0305004100023197"
CN_TITLE = "A practical method for numerical evaluation of solutions of partial differential equations of the heat-conduction type"
TEX = r"""\title{A note on explicit heat stepping}
\section{Method}
The update is
\begin{equation}\label{eq:update}
T^{n+1}_{i,j} = T^n_{i,j} + r\,(T^n_{i+1,j} + T^n_{i-1,j} + T^n_{i,j+1} + T^n_{i,j-1} - 4T^n_{i,j}),\quad r = \frac{\alpha\,\Delta t}{\Delta x^2}.
\end{equation}
It is stable for $r \le 1/4$.
"""


def tiny_pdf(pages):
    """A real PDF with one line of text per page (Helvetica), for pypdf to read."""
    n, objects = len(pages), ["<< /Type /Catalog /Pages 2 0 R >>"]
    objects.append(f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(n))}] /Count {n} >>")
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {3 + 2 * n} 0 R >> >>"
                       f" /Contents {4 + 2 * i} 0 R >>")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "heat_project"
    root.mkdir()
    code = (SAMPLE / "heat2d.py").read_text(encoding="utf-8").replace(
        "def step(T, r):", f"def step(T, r):  # time stepping compared with Crank & Nicolson (1947), doi:{CN_DOI}")
    (root / "heat2d.py").write_text(code, encoding="utf-8")
    (root / "README.md").write_text("# Heat solver\nThe stencil follows arXiv:2101.01234v2.\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "refs.bib").write_text(
        "@book{leveque2007,\n  title = {Finite Difference Methods for {Ordinary} and {Partial} Differential Equations},\n"
        "  author = {LeVeque, Randall J.},\n  year = {2007},\n  publisher = {SIAM},\n  doi = {10.1137/1.9780898717839}\n}\n"
        f"@article{{cn,\n  title = \"{CN_TITLE}\",\n  author = {{Crank, J. and Nicolson, P.}},\n  year = 1947,\n  doi = {{{CN_DOI}}}\n}}\n",
        encoding="utf-8")
    (root / "papers").mkdir()
    (root / "papers" / "note.tex").write_text(TEX, encoding="utf-8")
    (root / "papers" / "note.pdf").write_bytes(tiny_pdf(["Explicit heat stepping, page one", "The scheme is stable for r at most one quarter"]))
    (root / ".env").write_text("CREAO_HUB_KEY=capi_decoy_secret_value_123  # doi:10.9999/never.read\n")
    return root


@pytest.fixture
def connector(tmp_path, project):
    registry = GrantRegistry(state_file=tmp_path / "grants.json")
    registry.grant(project)
    return TestClient(create_service(registry=registry, http=SimpleNamespace()))


class Local:
    """The connector as the backend reaches it (the test client takes no timeout)."""

    def __init__(self, client):
        self.client = client

    def post(self, url, json, timeout, headers=None):
        return self.client.post(url, json=json, headers=headers)


class Creao:
    def __init__(self):
        self.replies, self.calls = [], []

    def run(self, inputs):
        self.calls.append(inputs)
        return SimpleNamespace(reply=self.replies.pop(0), run_id=f"run-{len(self.calls)}")


class Papers:
    """Crossref, faked: what was asked, and a small index of real works."""
    WORKS = {CN_DOI: {"doi": CN_DOI, "title": CN_TITLE, "authors": "J. Crank, P. Nicolson", "families": ["crank", "nicolson"],
                      "year": 1947, "venue": "Mathematical Proceedings of the Cambridge Philosophical Society", "type": "journal-article"},
             "10.1137/1.9780898717839": {"doi": "10.1137/1.9780898717839", "title": "Finite Difference Methods for Ordinary and "
                                         "Partial Differential Equations", "authors": "Randall J. LeVeque", "families": ["leveque"],
                                         "year": 2007, "venue": "", "type": "book"}}

    def __init__(self, down=False):
        self.asked, self.down = [], down

    def by_doi(self, doi):
        self.asked.append(doi)
        if self.down:
            raise LookupFailed("offline")
        return self.WORKS.get(doi)

    def search(self, title, author="", year=None):
        self.asked.append(f"{title} | {author} | {year}")
        if self.down:
            raise LookupFailed("offline")
        return [w for w in self.WORKS.values() if likeness(w["title"], title) > 0.5]


# LaTeX the way agents often send it: single backslashes, so JSON reads \f, \t and \n as escapes
# and rejects \a, \D and \l. The lesson must still come out right.
REPLY = r"""I read the paper and the code.
```json
{"lesson": {"title": "The explicit heat update", "summary": "Each step adds r times the discrete Laplacian.",
 "paper": {"title": "A note on explicit heat stepping", "authors": "A. Author", "year": 2024, "venue": "Notes"},
 "sources": [{"ref": "R1", "title": "", "why": "The time stepping is compared with it."},
             {"ref": "", "title": "Finite Difference Methods for Ordinary and Partial Differential Equations", "authors": "R. J. LeVeque",
              "year": 2007, "venue": "SIAM", "doi": "", "why": "The 5-point Laplacian and the stability limit."},
             {"ref": "", "title": "Heat flow in imaginary lattices", "authors": "N. Nobody", "year": 2019, "doi": "10.1017/S0305004100023197",
              "why": "Made up."}],
 "equations": [{"name": "Explicit update", "paper_ref": "Eq. (1), Section 1", "latex": "T_{i,j}^{n+1} = T_{i,j}^n + r\left(\nabla^2 T\right)_{i,j}",
                "plain": "T_new = T + r * lap", "meaning": "Forward Euler on the heat equation.",
                "terms": [{"symbol": "r", "meaning": "diffusion number", "units": "", "code": ["r"], "lines": "24"},
                          {"symbol": "\theta", "meaning": "not used here", "code": ["kappa"], "lines": "99"},
                          {"symbol": "\Delta t", "meaning": "time step", "units": "s", "code": ["dt", "cfg.dt"], "lines": "31"}],
                "in_code": "Line 23 builds the Laplacian and line 24 adds r times it.", "lines": "23-24", "sources": [1, 7]},
               {"name": "Diffusion number", "latex": "r = \frac{\alpha \Delta t}{\Delta x^2}", "plain": "r = alpha*dt/dx^2",
                "terms": [], "in_code": "Line 32.", "lines": "32"},
               {"name": "Unsafe", "latex": "\href{http://example.com}{x}", "plain": "x"}],
 "walkthrough": [{"lines": "18-25", "text": "step() updates every inner cell."}, {"lines": "500", "text": "Not in the code."}],
 "pictures": [
   {"type": "flow", "title": "One run", "caption": "What main() does.", "steps": [{"label": "Read the config", "lines": "29-31"},
     {"label": "Compute r", "lines": "32"}, {"label": "Update every cell", "detail": "step()", "lines": "41"},
     {"label": "Stop on NaN", "lines": "45-48"}], "loop": {"from": 2, "to": 3, "label": "for each time step"}},
   {"type": "grid", "title": "The 5-point stencil", "rows": 3, "cols": 3, "row_label": "i", "col_label": "j", "lines": "23",
     "cells": [{"row": 1, "col": 1, "label": "-4", "tone": "focus"}, {"row": 0, "col": 1, "label": "+1", "tone": "uses"},
               {"row": 2, "col": 1, "label": "+1", "tone": "uses"}, {"row": 1, "col": 0, "label": "+1", "tone": "uses"},
               {"row": 1, "col": 2, "label": "+1", "tone": "uses"}, {"row": 7, "col": 7, "label": "off"}],
     "arrows": [{"from": [0, 1], "to": [1, 1]}, {"from": [1, 1], "to": [1, 1]}, {"from": [0, 1], "to": [9, 9]}]},
   {"type": "plot", "title": "Growth per step", "x": {"label": "r", "from": 0, "to": 0.5}, "y": {"label": "|g|"},
     "curves": [{"label": "worst mode", "expr": "abs(1 - 8*x)"}, {"label": "stable bound", "expr": "1 + 0*x"}],
     "marks": [{"x": 0.25, "label": "r = 1/4"}, {"x": 3, "label": "off the axis"}]},
   {"type": "plot", "title": "Sneaky", "x": {"from": 0, "to": 1}, "curves": [{"expr": "__import__('os').system('echo hi')"}]}],
 "caveats": ["Stable only for r <= 1/4."], "check_yourself": ["What happens if dt doubles?"],
 "finding_ids": ["not-offered"]}}
```"""


@pytest.fixture
def lab(tmp_path, connector):
    creao, papers = Creao(), Papers()
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=lambda prefix: creao, data_dir=data, papers=papers,
                                   chat_setup=lambda: ChatSettings("CHAT", "test"),
                                   connector=ConnectorClient("", Local(connector))))
    headers = {m: {"Authorization": f"Bearer {client.post('/sign-in', json={'member_id': m}).json()['token']}"}
               for m in ("researcher_a", "researcher_b", "leader")}
    return SimpleNamespace(client=client, creao=creao, papers=papers, headers=headers, db=Database(data))


def ask(lab, member="researcher_a", **body):
    """A lesson on the whole of heat2d.py with the LaTeX paper, unless body says otherwise."""
    return lab.client.post("/lessons", headers=lab.headers[member], json={
        "project_id": "heat-diffusion", "folder": "heat_project", "path": "heat2d.py",
        "paper_path": "papers/note.tex", **body})


def test_the_connector_reads_the_whole_code_file(connector, project):
    whole = connector.post("/tools/read_code", json={"folder": "heat_project", "path": "heat2d.py"}).json()
    assert whole["lines"] == "1-59" and whole["total_lines"] == 59 and not whole["truncated"]
    assert "   24|             new[i][j] = T[i][j] + r * lap" in whole["text"]
    (project / "long.f90").write_text("".join(f"  x({i}) = x({i}) + dt * rhs({i})  ! cell {i}\n" for i in range(1, 3001)))
    long = connector.post("/tools/read_code", json={"folder": "heat_project", "path": "long.f90"}).json()
    assert long["truncated"] and long["total_lines"] == 3000 and len(long["text"]) <= 60_000
    assert long["lines"] == f"1-{long['text'].count(chr(10)) + 1}"  # whole lines only, from the start
    assert connector.post("/tools/read_code", json={"folder": "heat_project", "path": ".env"}).status_code == 403
    assert connector.post("/tools/read_code", json={"folder": "heat_project", "path": "papers/note.pdf"}).status_code == 403


# --- The paper and the project's own references, through the connector ----------------------------


def test_the_connector_reads_the_paper_given(connector, monkeypatch):
    listed = connector.post("/tools/list_papers", json={"folder": "heat_project"}).json()
    assert set(listed["files"]) == {"README.md", "papers/note.tex", "papers/note.pdf"}  # never .env or code

    tex = connector.post("/tools/read_paper", json={"folder": "heat_project", "path": "papers/note.tex"}).json()
    assert tex["kind"] == "tex" and r"\frac{\alpha\,\Delta t}{\Delta x^2}" in tex["text"] and not tex["truncated"]

    pdf = connector.post("/tools/read_paper", json={"folder": "heat_project", "path": "papers/note.pdf"}).json()
    assert pdf["kind"] == "pdf" and pdf["total_pages"] == 2 and pdf["pages"] == "1-2"
    assert "[page 1]\nExplicit heat stepping, page one" in pdf["text"] and "[page 2]" in pdf["text"]
    second = connector.post("/tools/read_paper", json={"folder": "heat_project", "path": "papers/note.pdf", "pages": "2"}).json()
    assert second["pages"] == "2-2" and "page one" not in second["text"] and "one quarter" in second["text"]
    beyond = connector.post("/tools/read_paper", json={"folder": "heat_project", "path": "papers/note.pdf", "pages": "5-6"})
    assert beyond.status_code == 422 and "has 2 pages" in beyond.json()["detail"]
    assert connector.post("/tools/read_paper", json={"folder": "heat_project", "path": "heat2d.py"}).status_code == 403
    assert connector.post("/tools/read_paper", json={"folder": "heat_project", "path": ".env"}).status_code == 403

    monkeypatch.setitem(sys.modules, "pypdf", None)  # pypdf not installed
    missing = connector.post("/tools/read_paper", json={"folder": "heat_project", "path": "papers/note.pdf"})
    assert missing.status_code == 422 and 'expandrg[papers]' in missing.json()["detail"]
    assert connector.post("/tools/read_paper", json={"folder": "heat_project", "path": "papers/note.tex"}).status_code == 200


def test_the_connector_finds_the_papers_the_project_cites(connector):
    found = connector.post("/tools/project_references", json={"folder": "heat_project", "path": "heat2d.py"}).json()
    refs = found["references"]
    assert refs[0] == {"id": "R1", "kind": "doi", "doi": CN_DOI, "line": 18, "file": "heat2d.py",
                       "context": f"def step(T, r):  # time stepping compared with Crank & Nicolson (1947), doi:{CN_DOI}"}
    assert {"kind": "arxiv", "arxiv": "2101.01234", "file": "README.md", "line": 2}.items() <= refs[1].items()
    bib = refs[2]
    assert bib["title"] == "Finite Difference Methods for Ordinary and Partial Differential Equations"  # braces removed
    assert (bib["authors"], bib["year"], bib["venue"], bib["doi"]) == ("LeVeque, Randall J.", 2007, "SIAM", "10.1137/1.9780898717839")
    assert len(refs) == 3  # the BibTeX entry for Crank-Nicolson is the same DOI as R1, so it's listed once
    assert ".env" not in found["files"] and "never.read" not in json.dumps(found)
    assert connector.post("/tools/project_references", json={"folder": "heat_project", "path": ".env"}).status_code == 403
    assert connector.post("/tools/project_references", json={"folder": "heat_project", "path": "../x.py"}).status_code == 403


# --- Checking the lesson against the code that was sent ---------------------------------------


def excerpt(connector, lines="1-60"):
    return connector.post("/tools/read_project_file", json={"folder": "heat_project", "path": "heat2d.py", "lines": lines}).json()


def test_latex_survives_json_and_the_lesson_is_checked_against_the_code(connector):
    refs = [{"id": "R1", "doi": CN_DOI, "file": "heat2d.py", "line": 18}]
    lesson = parse_lesson(lesson_block(REPLY), excerpt(connector), refs, [{"id": "f1"}])
    first, second, unsafe = lesson["equations"]
    assert first["latex"] == r"T_{i,j}^{n+1} = T_{i,j}^n + r\left(\nabla^2 T\right)_{i,j}" and first["paper_ref"] == "Eq. (1), Section 1"
    assert second["latex"] == r"r = \frac{\alpha \Delta t}{\Delta x^2}"
    assert first["terms"][1]["symbol"] == r"\theta" and first["terms"][2]["symbol"] == r"\Delta t"
    assert unsafe["latex"] == "" and "doesn't draw" in unsafe["latex_note"] and unsafe["plain"] == "x"
    # names in the code are checked; lines must be lines that were sent
    assert first["terms"][0]["code"] == [{"name": "r", "found": True}] and first["terms"][0]["lines"] == [24, 24]
    assert first["terms"][1]["code"] == [{"name": "kappa", "found": False}] and "weren't in the code sent" in first["terms"][1]["lines_note"]
    assert first["terms"][2]["code"] == [{"name": "dt", "found": True}, {"name": "cfg.dt", "found": False}]
    code = Code(excerpt(connector)["text"])
    assert [code.name(n)["found"] for n in ('cfg["dt"]', "T[i][j  + 1]", "i, j", "i / (n - 1)", 'cfg["kappa"]', "i, kappa")] == \
        [True, True, True, True, False, False]  # expressions as written in the code, spacing aside
    assert first["lines"] == [23, 24] and first["sources"] == [1]  # source 7 doesn't exist
    assert lesson["walkthrough"][1]["lines_note"] and "lines" not in lesson["walkthrough"][1]
    assert lesson["finding_ids"] == []  # only findings that were offered
    assert lesson["sources"][0]["ref"] == "R1" and lesson["sources"][2]["doi"] == CN_DOI
    assert lesson["paper"] == {"title": "A note on explicit heat stepping", "authors": "A. Author", "year": 2024, "venue": "Notes"}
    assert [p["type"] for p in lesson["pictures"]] == ["flow", "grid", "plot"]
    assert len(lesson["picture_notes"]) == 1 and "Sneaky" in lesson["picture_notes"][0]

    narrow = parse_lesson(lesson_block(REPLY), excerpt(connector, "18-25"), refs, [])
    assert narrow["equations"][1].get("lines") is None and narrow["equations"][1]["lines_note"]  # line 32 wasn't sent
    with pytest.raises(LessonUnreadable):
        parse_lesson(lesson_block("No JSON here."), excerpt(connector), refs, [])
    with pytest.raises(LessonUnreadable):
        parse_lesson({"title": "", "summary": "x"}, excerpt(connector), refs, [])


# --- Pictures: descriptions ExpandRG draws, with plots computed here ------------------------


def test_pictures_are_checked_and_plots_are_computed(connector):
    code = Code(excerpt(connector)["text"])
    pictures, notes = check_pictures(lesson_block(REPLY)["pictures"], code.lines)
    flow, grid, plot = pictures
    assert [s["label"] for s in flow["steps"]] == ["Read the config", "Compute r", "Update every cell", "Stop on NaN"]
    assert flow["steps"][1]["lines"] == [32, 32] and flow["loop"] == {"from": 2, "to": 3, "label": "for each time step"}
    assert len(grid["cells"]) == 5 and grid["cells"][0] == {"row": 1, "col": 1, "label": "-4", "tone": "focus"}  # (7, 7) is off the grid
    assert grid["arrows"] == [{"from": [0, 1], "to": [1, 1], "label": ""}]  # no self-arrow, nothing off the grid
    worst = dict(map(tuple, plot["curves"][0]["points"]))
    assert len(worst) == 121 and worst[0.0] == 1.0 and worst[0.25] == 1.0 and worst[0.5] == 3.0  # |1 - 8r|, computed
    assert plot["computed"] and plot["marks"] == [{"x": 0.25, "label": "r = 1/4"}]
    assert "uses something other than x" in notes[0]

    gaps = compile_formula("log(x)")
    assert gaps(-1.0) is None and gaps(0.0) is None and gaps(1.0) == 0.0  # undefined: a gap, not an error
    assert compile_formula("x^2")(3.0) == 9.0  # ^ is a power
    assert compile_formula("9**9**9**x")(1.0) is None  # overflows at once, as a float
    for bad in ("x.__class__", "(lambda: 1)()", "open('f')", "[x for x in y]", "x if x else 1", "exp", "'a' * 3", "1j * x"):
        with pytest.raises(FormulaRefused):
            compile_formula(bad)
    assert check_pictures([{"type": "svg", "svg": "<script/>"}], code.lines)[0] == []


# --- Papers, on Crossref ------------------------------------------------------------------------


def test_each_paper_is_marked_given_cited_found_or_not_found():
    refs = [{"id": "R1", "doi": CN_DOI, "file": "heat2d.py", "line": 18}, {"id": "R2", "arxiv": "2101.01234", "file": "README.md", "line": 2}]
    sources = [{"provided": True, "file": "papers/cn.pdf", "title": CN_TITLE, "authors": "J. Crank", "year": 1947},
               {"ref": "R1", "title": ""}, {"ref": "R2", "title": "A preprint"},
               {"ref": "", "title": "Finite difference methods for ordinary and partial differential equations", "authors": "LeVeque, R.", "year": 2007},
               {"ref": "", "title": "Heat flow in imaginary lattices", "authors": "N. Nobody", "year": 2019, "doi": CN_DOI},
               {"ref": "", "title": "Finite Difference Methods for Ordinary and Partial Differential Equations", "authors": "A. Someone", "year": 2007},
               {"ref": "", "title": "Something nobody wrote", "authors": "N. Nobody", "year": 2020},
               {"ref": "", "title": "", "doi": CN_DOI}]
    given, cited, preprint, found, mismatch, other_author, missing, doi_only = check_sources(sources, refs, Papers())
    assert given["status"] == "provided" and given["url"] == f"https://doi.org/{CN_DOI}" and "papers/cn.pdf" in given["note"]
    assert cited["status"] == "project" and "heat2d.py, line 18" in cited["note"] and cited["url"] == f"https://doi.org/{CN_DOI}"
    assert cited["record"]["year"] == 1947
    assert preprint["status"] == "project" and preprint["url"] == "https://arxiv.org/abs/2101.01234"
    assert found["status"] == "crossref" and found["doi"] == "10.1137/1.9780898717839"
    assert mismatch["status"] == "doi_mismatch" and mismatch["doi"] == "" and CN_TITLE in mismatch["note"]
    assert other_author["status"] == "crossref"  # a different first author, but the identical title
    assert missing["status"] == "not_found" and "may be wrong" in missing["note"]
    assert doi_only["status"] == "crossref" and doi_only["record"]["title"] == CN_TITLE  # a DOI alone is enough

    offline = check_sources(sources, refs, Papers(down=True))
    assert offline[0]["status"] == "provided" and offline[1]["status"] == "project"  # still given, still cited
    assert {s["status"] for s in offline[3:]} == {"unchecked"}


def test_crossref_records_are_read_from_its_json():
    class Http:
        def __init__(self, *answers):
            self.answers, self.urls = list(answers), []

        def get(self, url, **kwargs):
            self.urls.append((url, kwargs.get("params")))
            status, body, headers = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            return SimpleNamespace(status_code=status, json=lambda: body, headers=headers)

    body = {"message": {"DOI": CN_DOI, "title": [f"<i>{CN_TITLE}</i>"], "author": [{"given": "J.", "family": "Crank"}, {"given": "P.", "family": "Nicolson"}],
                        "issued": {"date-parts": [[1947, 1]]}, "container-title": ["Math. Proc. Camb. Phil. Soc."], "type": "journal-article"}}
    waits = []
    http = Http((429, {}, {"Retry-After": "2"}), (200, body, {}))  # asked too fast, then answered
    record = CrossrefLookup(http, sleep=waits.append).by_doi(CN_DOI)
    assert record["title"] == CN_TITLE  # markup removed
    assert record["authors"] == "J. Crank, P. Nicolson" and record["year"] == 1947 and record["families"] == ["crank", "nicolson"]
    assert http.urls[0][0] == f"https://api.crossref.org/works/{CN_DOI}" and len(http.urls) == 2 and 2.0 in waits
    assert CrossrefLookup(Http((404, {}, {})), sleep=waits.append).by_doi("10.1/none") is None
    busy = Http((503, {}, {}))
    with pytest.raises(LookupFailed):
        CrossrefLookup(busy, sleep=waits.append).search("x")
    assert len(busy.urls) == 4  # asked four times before giving up


# --- The whole flow, through the API ----------------------------------------------------------


def test_a_lesson_is_written_from_the_paper_and_code_and_kept_private(lab):
    lab.creao.replies.append(REPLY)
    started = ask(lab)  # no question: the Super Brain writes the whole lesson
    assert started.status_code == 202, started.text
    assert started.json()["references"] == 3 and started.json()["paper"]["kind"] == "tex"
    job = lab.client.get(f"/jobs/{started.json()['job_id']}", headers=lab.headers["researcher_a"]).json()
    assert job["status"] == "completed", job
    lesson_id = job["diagnosis"]["lesson_id"]

    sent = lab.creao.calls[0]["context"]
    assert "lap = T[i + 1][j]" in sent and CN_DOI in sent and "eq:update" in sent  # the code, its references and the paper
    assert "(none: write the whole lesson)" in sent
    assert "capi_decoy" not in sent and "never.read" not in sent
    assert all("lap" not in asked and "def step" not in asked and "eq:update" not in asked for asked in lab.papers.asked)

    listed = lab.client.get("/lessons?project_id=heat-diffusion", headers=lab.headers["researcher_a"]).json()
    # the whole file was read
    assert [(l["id"], l["title"], l["path"], l["lines"], l["paper"]) for l in listed] == \
        [(lesson_id, "The explicit heat update", "heat2d.py", "1-59", "papers/note.tex")]
    full = lab.client.get(f"/lessons/{lesson_id}", headers=lab.headers["researcher_a"]).json()
    lesson = full["lesson"]
    assert [s["status"] for s in lesson["sources"]] == ["provided", "project", "crossref", "doi_mismatch"]
    assert lesson["sources"][0]["title"] == "A note on explicit heat stepping"
    assert lesson["equations"][0]["sources"] == [2]  # the agent's source 1, after the paper given
    assert full["paper"] == {"folder": "heat_project", "path": "papers/note.tex", "kind": "tex", "pages": "", "truncated": False}
    assert "eq:update" not in json.dumps(full)  # the paper's text isn't kept
    assert "   23| " in full["code"] and full["creao_run_id"] == "run-1"
    assert [r["id"] for r in lesson["references"]] == ["R1", "R2", "R3"]
    assert [p["type"] for p in lesson["pictures"]] == ["flow", "grid", "plot"]

    # private: nobody else can list or open it, and nothing reached the shared database
    assert lab.client.get(f"/lessons/{lesson_id}", headers=lab.headers["researcher_b"]).status_code == 404
    assert lab.client.get("/lessons?project_id=heat-diffusion", headers=lab.headers["researcher_b"]).json() == []
    assert lab.client.get(f"/lessons/{lesson_id}", headers=lab.headers["leader"]).status_code == 403
    with closing(lab.db.shared()) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "lessons" not in tables

    assert lab.client.delete(f"/lessons/{lesson_id}", headers=lab.headers["researcher_a"]).json() == {"status": "deleted"}
    assert lab.client.get(f"/lessons/{lesson_id}", headers=lab.headers["researcher_a"]).status_code == 404


def test_lessons_refuse_what_they_cannot_read_and_say_why(lab):
    assert ask(lab, member="leader").status_code == 403
    assert ask(lab, project_id="wave-propagation", member="researcher_b").status_code == 403  # not B's project
    missing = ask(lab, path="nowhere.py")
    assert missing.status_code == 422 and "There is no nowhere.py" in missing.json()["detail"]
    assert ask(lab, path=".env").status_code == 422
    assert ask(lab, lines="900-950").status_code == 422  # lines (still allowed through the API) past the end of the file
    assert ask(lab, paper_path="heat2d.py").status_code == 422  # code isn't a paper
    pages = ask(lab, paper_path="papers/note.pdf", paper_pages="7")
    assert pages.status_code == 422 and "has 2 pages" in pages.json()["detail"]
    assert not lab.creao.calls  # none of these used credits

    lab.creao.replies.append("Sorry, I can't.")
    job = lab.client.get(f"/jobs/{ask(lab, question='How is r chosen?').json()['job_id']}", headers=lab.headers["researcher_a"]).json()
    assert job["status"] == "failed" and "no lesson in it" in job["error"]["message"]
    assert "How is r chosen?" in lab.creao.calls[0]["context"]
    assert lab.client.get("/lessons?project_id=heat-diffusion", headers=lab.headers["researcher_a"]).json() == []
