# Changelog

One entry per commit, newest first. Each commit adds its own entry with the hash set to `pending`; the next commit replaces `pending` with the real hash.

---

## 2026-10-25 · `pending` · ExpandRG 0.16.0: onboarding lessons, the Super Brain teaching a paper and its code

**Why:** someone new to a project has to learn its code and the paper behind it, mostly by asking colleagues. Now they give the Super Brain the paper and the code, and it writes the lesson.

- **Faster CREAO replies.** Most of a reply's time is CREAO's own run, 12 to 26 s, which the app can't change. What the app added on top is now about 1 s:
  - **Checking for the answer** (started in a separate session, finished here). The app checked every 5 s, so a finished answer could wait up to 5 s; now every 1 s. It also no longer doubles its wait after "too many requests": that once hid a finished run for 22 s, and a real personal-agent reply went from 39.1 s to 16.5 s.
  - **CREAO's rate limit.** CREAO refuses a key's 30th request within a minute (HTTP 429, "Retry-After: 33"). With a check every second, the third chat in a row waited out the penalty while its answer was ready: 34.5 s instead of 13. Now each agent's clients share a pace of 28 requests a minute, a 429 holds them all, and a run's first check waits for 75% of that agent's usual run time, learned from CREAO's own timestamps. That's 4 or 5 checks per answer instead of 13 to 25.
  - Measured on four Super Brain chats in a row: 13.3, 13.7, 12.9 and 13.7 s, with no 429.
  - **Tried and not kept** (12 live runs): the fast model `gemini-3-flash` for personal agents (20 to 26 s, against 17 to 21 s with their own model) and the "triage" analysis depth for the Super Brain (14 s, within the 12 to 20 s spread). The only big remaining saving is streaming the answer as it's written, which needs CREAO's paid developer key (`cr_sk_`).
  - **Fewer agent rounds.** Personal agents are told to ask for all their lookups at once, and not to search again for findings already given. Lookups in one round run at the same time: four 120 ms reads took 0.48 s, now 0.12 s.
  - **Other changes:** runs use CREAO's app-scoped routes. Every reply carries a timing breakdown (`latency`). The dashboard and VS Code check the backend for a finished job every 0.75 s (it was 1.2 to 3 s).
  - Checks: new `tests/test_creao_latency.py`, covering the timing breakdown, bounded retries, `Retry-After`, the shared pace and its hold, the timed first check and what it learns, and parallel but ordered lookups. `python -m pytest`: 613 passed, 2 skipped.
- **Super Brain → Lessons** (researchers; private to them, and no one is told). Pick the code (a file and lines) and the paper it implements, both from granted folders; a focus question is optional. **Preview what's sent**, then **Write the lesson**: one Super Brain run, about a minute. The lesson has:
  - the idea in plain words;
  - the paper's equations, drawn with KaTeX, each tagged with where it is in the paper;
  - a table of every term: meaning, units, the names in the code, and lines;
  - how the code does it, with the code beside the lesson: hovering or clicking a line chip highlights those lines;
  - **See it:** pictures ExpandRG draws from the Super Brain's descriptions: a flow of the code's steps (with its loop), stencil grids, and plots of the lesson's formulas;
  - a walkthrough, caveats, questions to check yourself, and matching group findings.
- **Checked before it's shown.**
  - Cited lines must be lines that were sent.
  - Names must be in the code (a name like `cfg["dt"]` is matched as written); otherwise they're struck through.
  - LaTeX that could load or define anything is dropped.
  - A plot's formulas are evaluated by ExpandRG with a restricted evaluator (arithmetic, a few functions, x, pi, e), so its curves are computed, never invented.
  - The agent never sends SVG, HTML or code.
- **Papers checked on Crossref**, sending only titles, first authors, years and DOIs. Each paper is tagged: the paper you gave, cited in the project (with its file and line), found on Crossref (DOI link), a DOI that belongs to another paper, not found (may be wrong), or not checked. The project's own citations are found on your computer: DOIs and arXiv IDs in the code, README and CITATION.cff, and BibTeX entries.
- **Connector:** `/tools/list_papers`, `/tools/read_paper` (a PDF page by page, or `.tex`, `.md`, `.txt`; about 10 pages, or the pages you pick) and `/tools/project_references`. PDFs need the new optional extra, `pip install "expandrg[papers]"` (pypdf); without it a PDF says what to install. The folder walker can now offer PDFs when asked to.
- **Backend:** `backend/onboarding/` (lessons, papers, pictures); `POST /lessons` (a job), `GET /lessons`, `GET`/`DELETE /lessons/{id}`. New private table `lessons` (the paper's text isn't kept), added on start with no reset. A lesson the Super Brain left empty fails with a readable reason.
- **Dashboard:** `LessonsView.jsx`; KaTeX 0.18.11 (0.16.11 and 0.16.47 have published advisories), loaded only on Lessons. Worksheets and Lessons use the full width.
- Docs: README (Lessons), `backend/README.md`, `Documents/dashboard-design.md`, `frontend/README.md`, build plan step 12, and the Context Engine guide (lessons don't register context artifacts yet).
- Checks:
  - New `tests/test_lessons.py` (8 tests; the real connector, CREAO and Crossref faked). It covers reading `.tex` and PDF papers, pages, and refusals; the project's citations; LaTeX surviving JSON; line, name and finding checks; pictures and the formula evaluator refusing anything but arithmetic; every paper status, including Crossref down or rate-limited; the whole flow, kept private; refusals that use no credits.
  - `python -m pytest`: 592 passed, 2 skipped, at the time. `npm run build` succeeds.
  - **With real CREAO**, through the dashboard on a scratch copy of the demo data: a short LaTeX methods note and `heat2d.py` lines 13–52 gave a lesson in 72 s, with:
    - 6 equations, each placed in the paper and drawn without errors, with term tables;
    - a flow, two stencil grids and an amplification-factor plot;
    - an 8-step walkthrough and caveats.

    Clicking a line chip highlighted lines 23–24, and there were no page errors. The run caught two problems, both fixed: names written as expressions were struck through, and Crossref's rate limit (HTTP 429) marked every later paper "not checked". Rechecked on the real Crossref, all four books and papers the Super Brain named were found, with DOIs. Plots now take the full row so their axis numbers stay readable, and labels on arrows between neighbouring grid cells are listed under the grid.
  - On a real Fortran project, a PDF saved into the project while Lessons was open didn't show in the paper list, and it was the last of 18 files behind the project's notes. The list now reloads whenever it's opened, and shows PDFs and `.tex` first, under **Papers**, with notes and text files below. The connector read that PDF (Cortez 2001, pages 1–16 of 22).
  - **A clearer New lesson form**, from the team's review:
    - The paper and the code are two cards side by side. Each shows what's read: a PDF's pages, a file's lines.
    - **The whole code file is read** (new connector tool `/tools/read_code`, up to 60,000 characters with line numbers, about 1,200 lines), so the From line / To line boxes are gone. The API still takes `lines`.
    - Code files are listed first, then other files.
    - The paper's folder picker appears only on "Paper in another folder?".
    - The exact text sent is under **See exactly what's sent**.
    - The intro now just says what happens, without "New to <project>?", and the credits line under the form is gone.
    - Checked in Chrome, light and dark, on the Fortran project: the paper card read 22 pages (1–16 sent) and the code card read all 932 lines of `main.f90`. No page errors. One new test reads a whole file and a 3,000-line one, which is cut at whole lines.
  - **The Project page's heading is the project folder's name**, not the group's project name: in the demo it read "Heat diffusion solver" over another codebase's overview. The overview now saves the mapped folder's name (`map_stats.folder`; no table change). While building, the heading is the folder picked. An overview saved before this shows "Project overview" until **Refresh overview**. A test checks the name is saved.
  - **Saved versions: delete, and an update box on each.**
    - **Delete this version** removes its saved code, inputs, working copy and output from this computer. It's refused while the version runs; the project folder is never touched, and its results in Monitor stay. New connector route: `DELETE /simulations/{id}`. Read-only files left by a copied `.git` are cleared first, and if Windows still holds a file, the version is kept so it can be retried.
    - **Tell your agent more about this version:** a box in the version's card, sent with **Update setup** for that version. Before, the only box was the one for preparing a new version, at the top of the page.
    - A version that already ran keeps its setup as the record of that run, so **Update as a new version** updates a copy.
    - The agent's questions now say to answer in the box below.
    - New test: delete refused for another researcher (404) and while running (409); deleted for good, even after a restart; the project folder unchanged.
  - **Set up & run refused real projects** with "source_files must be a short list of text." The agent's plan for a Fortran project cited its 19 source files, and a plan could cite at most 12. Now:
    - a plan can cite up to 80 files;
    - questions and environment notes are cut to 12 instead of failing;
    - non-text entries are dropped;
    - Windows paths (`src\main.f90`) are matched like `src/main.f90`.

    A cited file that isn't in the project still refuses the plan, and the message now names it. New test: a 19-module Fortran plan.
  - **Old runs in Monitor and Visualize.** Those tabs only showed runs ExpandRG started or watched, and only numbers written `step 200  residual = 0.004`. A Fortran run's folder (a `run.log` printing `z-drift/beat  -2.077E-02   steadiness  0.002` per beat, and `fort.N` files) showed nothing. New command, read-only: `python -m local_connector import-runs <granted folder> --project <id> --as <member>`. It finds run folders under the granted folder and reads:
    - log lines made only of names and numbers, numbered in order;
    - one-number-per-line `fort.N` files, named from the run's own source (`! fort.90 : body-only power`, else the variable written). Multi-column files are left out, and so are big unnamed ones (values per node).

    Up to 6 numbers per run (the backend's limit): the log's repeated ones first. NaN or Inf gives a critical alert. A folder already imported is skipped. New module `local_connector/simulation_monitor/old_runs.py`, new test `tests/test_old_runs.py`.
  - **The group's swimmer runs get their usual graphs:** distance from start, velocity and efficiency per beat, and nothing else. They're worked out as the group's `compare_v3` does with its original "chord" velocity:
    - velocity is the distance the rod's centre (`fort.10`, `np` points a frame) moves in a beat;
    - power is `fort.80`'s energy per beat divided by the beat period (1/omFac);
    - efficiency = velocity² / power;
    - the first beat starts from rest, so it only has a distance.

    Frames per beat come from `dt`, `omFac` (`parameters.f90`) and `nw` (`setup.txt`). Checked against the group's own numbers: `bare_sine_2w` beat 10 gives velocity 0.02076, as `compare_bare_2waves.txt` says. Two old runs (`bare_sine_2w`, 10 beats; `g044`, 20 beats) were imported into the demo. They show in Monitor and Visualize, and the first import of them, with the log's numbers, was removed.
  - **Devpost:** the README has a **CREAO agents** section (what the Super Brain and personal agents do, and the Super Brain's agent ID), and the write-up is in `Documents/devpost.md`.
  - **No Experiments page.** The team tries changes as new versions under Simulations → Set up & run, so the page and its sidebar item are gone (`ExperimentsView.jsx` deleted). The connector's `/experiments` API is unchanged. Worksheets' "Try it" step and Visualize's empty message now point to Set up & run. The README and the dashboard docs say so.
  - **A makefile comment broke the build.** On the Fortran project, `FC = gfortran  # compiler` became the compiler `gfortran # compiler`, so `{fortran}` builds failed with "linker input file not found". A trailing `# comment` on a makefile's `FC`, `CC` or `CXX` line is now left out. New test in `tests/test_runtime_environment.py`. Versions prepared before this keep the compiler they saved; prepare a new one.
  - **A project is named after its main folder, for everyone.** The sidebar, top bar and pages showed the demo's "Heat diffusion solver" over another codebase. Building a Project overview now renames the project to the folder that was mapped (`rename_project`; its ID and everything filed under it stay), and updates `group.json` in a group, so every member sees the same name. The overview itself stays private. Tested: after Researcher A's overview, A, B and the leader all see the folder's name, and B still can't open A's overview. In the installed demo, the project was renamed to the folder Researcher B's overview had already mapped.
  - **Remembered after closing the browser:** who's signed in, the project, the last graph and the last folder picked were kept only for the open tab, so after a restart you could come back as another researcher and not see your own (private) lessons and overview. They're now also kept in the browser. The open tab's own choice still wins, so two windows can be two people. Folder pickers pick the last folder again.
  - **Diagnose names the folder too:** "Diagnose a problem in <folder>", or "Diagnose a problem" before one is picked. Its file list is a 320 px box that scrolls, with the column names kept in view; above it the number of files and how many are selected, and under it the files selected. Checked on the Fortran project: 44 files in the box, two ticked and listed.
  - **Write the lesson** was greyed out until **Preview what's sent** had been clicked, and nothing said so. The preview now loads by itself once a code file is picked, and after any change (the button is now **Reload what's sent**). While **Write the lesson** is off, a line says why ("Pick a code file in step 1."). Checked on that project in the installed 0.16.0: off with that line, then on after picking `main.f90` and the PDF.
  - **Version 0.16.0** in the package, the extension, its lockfile and the install examples. `python build_package.py --vsix`: the wheel (155 files, 2.7 MB with the KaTeX chunk, no keys or databases) and `expandrg-0.16.0.vsix` (15 files, 2.6 MB, carrying it); 34 extension tests passed. Installed with `[papers]` into a new virtual environment outside the repo, it ran from that environment, had pypdf and the Lessons page, and answered `/lessons`.

---

## 2026-10-25 · `bc225e7` · ExpandRG 0.15.0: Research Context Engine

- Connect existing simulation runs, anomalies, private investigations, selected evidence, graph specs, worksheet teaching references and findings through typed references. Use additive artifact, relationship and receipt tables in the existing shared/private SQLite files; schema version 2 and existing data remain intact.
- Capture exact prepared code/input fingerprints, declared effective parameters, optional Git HEAD, repeat parents and diagnostic origins. Experiments now execute in a separate working copy so a solver cannot rewrite its retained evidence. Legacy metadata stays explicitly unknown.
- Give the existing CREAO toolkit bounded context and parameter searches, plus private unverified relationship proposals. Recheck membership and both relationship endpoints; exclude human comments and worksheet answers. Separate private analysis from the context eligible for automatic group hypotheses.
- Persist simulation/anomaly/evidence references with diagnosis episodes, separately from provider execution IDs. Approval reuses the explicit source run while keeping private investigations and evidence private.
- Make run starts and frozen report batches replay-safe, with atomic domain writes and receipts, stale-revision protection, durable connector delivery state and an authenticated local metadata outbox.
- Update the architecture proposal, implementation contracts, toolkit guide and build plan. Align wheel and extension versions at 0.15.0.
- Validation: 584 Python tests passed, 2 skipped; frontend build and all 34 extension tests passed. Three additional release checks verify package/source parity, isolated installed-wheel API operation and existing-database preservation. CREAO is mocked throughout these checks; no paid provider calls were made.

---

## 2026-10-25 · `1b0a067` · ExpandRG 0.14.1: recover when Visualize cannot load

- An older backend left running after a frontend update returned 404 for `/visualize/datasets`. The page kept showing “Loading your runs…” beside “Not Found.” Initialization now ends on failure, identifies this missing-endpoint case with restart/update guidance, and offers **Try again**.
- Deleted saved graphs fall back to a fresh graph. Transient saved-graph failures preserve the selection and can be retried. Opening another run cannot update a graph from the previous panel state.
- The actual idle demo runtime was restarted with current code, preserving its data. Visualize now returns the two existing datasets and draws through the dashboard proxy.
- Wheel and extension patch versions are aligned at 0.14.1.
- Checks: frontend production build and all 34 extension tests pass. Browser checks on the actual running dashboard cover missing-route guidance, stopping the failed loader, retry, deleted saved graphs, transient failures preserving selection, recovery, and rendering an existing run. Both the dataset and figure endpoints return 200 through the dashboard proxy. The rebuilt wheel and VSIX include the recovery fix.

---

## 2026-10-25 · `14010e9` · ExpandRG 0.14.0: dashboard redesign, Visualization Studio phases 1 and 2, worksheet creation, and installable packages

**Why:** ExpandRG only ran from a copy of this folder, and a group could only have the project it was set up with, so it couldn't be used on another project or another computer. The dashboard also gets the research-console design the team chose, and researchers can now compare runs on one graph.

- **Visualization Studio phase 2:**
  - Baseline absolute/relative differences on overlapping data, with explicit near-zero thresholds and gap counts; derived series are dashed. Shared-bin histograms and parameter sweeps use actual experiment parameters and final or interpolated values, with no extrapolation.
  - `expandrg.json` units are read from granted project folders for watched runs and experiments; researcher-set units win. **Read full log** explicitly rereads available originals, checks their saved samples still match, and keeps recorded data when a log moved, changed, or exceeds the limits.
  - **Diagnose this problem** on a run alert prepares an explicit diagnosis request. Private graphs show the affected measure around the alert and an earlier clean run when available. Checked graph operations from the personal CREAO agent are attached to the diagnosis and its draft; they open in Visualize.
  - Optional `expandrg[export]` installs Matplotlib for PDF, SVG, and PNG at specified physical sizes, DPI, and fonts. Exports render the same checked traces, bin widths, gaps, derived markers, and labels as the browser, on white.
  - Agent numeric claims with statistic references are checked against supplied data. Mismatches and unreferenced numeric answers are marked; general statements are not presented as verified observations.
  - Fixed sequential Undo, stale agent edits overwriting changes in another window, tiny scientific values not changing saved-data fingerprints, missing-data line bridging, malformed style errors, and overflow in comparisons.
  - Version 0.14.0 aligns the Python package, extension manifest, lockfile, and install examples so 0.13.0 installations can update.

- **Visualize: compare runs on interactive graphs, by hand or with your own agent** (build plan step 9; design in `Documents/visualization-design.md`).
  - **Where:** a new **Visualize** page (researchers: Simulation group; the leader: Group). **Open in Visualize** on a run (Monitor, Run results) and on an experiment comparison opens it with those runs. **Edit with Agent** there also puts the cursor in the agent box.
  - **By hand:** tick runs and experiments (each shows its changed parameters and alert counts), then pick:
    - measures (up to 3), the x axis (step, or a logged measure that increases with step), line or scatter;
    - runs together or in separate panels, a linear or log scale, a range of steps;
    - a title, a style (screen, paper, poster), units per measure, and notes at an x value.

    Each change is an operation ExpandRG checks before applying it. A refused one says why under the graph.
  - **The graph:** Plotly, loaded only on this page (its own 4.7 MB file, 1.4 MB compressed).
    - One panel per measure, never a second y axis.
    - Each run keeps its colour when others are added or removed. The 8 colours are checked for colour blindness on the light and dark backgrounds.
    - A legend for two or more runs, and labels at the line ends for up to four.
    - Red and amber lines mark each run's first Critical and Warning alert. With several runs in a panel, the notes say whose they are.
    - Under the graph: notes on what it can't show (a measure a run lacks, values a log scale can't show, a range that hides an alert, thinned points); the largest difference between runs where they overlap; a table of each line's points, lowest, highest, last value and first NaN/Inf, from every point.
  - **Accuracy:**
    - NaN and Inf stay gaps.
    - Comparisons use only the overlap. Values between measured points are straight-line estimates, never across a gap or beyond the data.
    - Long series are thinned for drawing only, keeping each bucket's lowest and highest value. Statistics use all points.
    - Nothing writes to runs, alerts or experiments.
  - **Saved graphs:**
    - **Save** names a graph. After that every change is saved as it's made.
    - **Undo** goes back one change, or one agent reply.
    - Saved graphs are private. They reopen from the current data and say if it changed since. The page reopens the last one you had open.
    - PNG and SVG export, always light on white so they print, whatever the page's theme.
  - **Edit with Agent** (researchers only; the leader has no personal agent): ask in plain words. The graph is saved first.
    - Your own CREAO agent gets the graph's settings, the runs you may use, and each line's statistics with a 40-point summary, never the full data. It may use its toolkit first.
    - It answers with a `graph_ops` block. ExpandRG checks each operation; refused ones go back to it once, with the reasons.
    - Applied ones are saved as the agent's, with the CREAO run ID.
    - Only you set units or request full-resolution reads. Baselines, differences, histograms, sweeps and optional PDF export are now available in Phase 2.
  - API: `GET /visualize/datasets`, `POST /visualize/figure`, `POST /visualize/ops`, `GET`/`POST /visualizations`, `GET`/`PUT`/`DELETE /visualizations/{id}`, `POST /visualizations/{id}/undo`, and `POST /visualizations/{id}/agent` (a job; poll `/jobs/{id}`). New private tables `visualizations` and `visualization_history`. New `backend/visualization/` (spec, sources, transforms, figure, ops, agent, store).
  - Also: the existing run charts' grid lines are solid instead of dashed. The dashboard adds `plotly.js-dist-min` 2.35.2.
  - Checks:
    - New `tests/test_visualization.py`, 13 tests: who can graph what; colours that stay with a run; alert lines and a range that hides one; log scales counting what they can't show; refused operations changing nothing; units set only by the researcher; scatter limited to 3 runs; alignment inside the overlap; thinning that keeps spikes; save, reopen, changed data and undo; Edit with Agent with one corrected retry; no writes to run data; the leader without an agent.
    - In Chrome, on a scratch copy of the demo data:
      - built a graph of both heat runs by clicking (measures, log scale, separate panels, range, title), saved, undid, and reopened it;
      - checked dark mode, **Open in Visualize** from Monitor, the leader's page (no agent panel) and a 900 px window;
      - PNG and SVG downloaded, and a PNG exported in dark mode is light on white.

      This caught six problems, all fixed: a squeezed graph (runs and controls now sit above a full-width graph); alert labels piling up; the table running past its card; one remembered graph shared by everyone on the computer; runs without numbers in the list; a Plotly error from line-end labels at values near 1e304 (those are now left to the legend).
    - **With real CREAO** (Researcher A's agent, through the page):
      - "Zoom in on steps 400 to 900 and use a log scale. Which one diverges first?" (62 s): both operations applied, its numbers matched the statistics, and it said the range hid alerts. One Undo restored the graph.
      - "Show residual instead" (20 s): applied.
      - "These results look wrong: residual goes past 1e290" (42 s): it said the run diverges, not the graph, from the statistics, and changed only the scale, to log, which keeps every value.

      Its first reply named a run by an internal ID, so it's now told to use the run's label.
    - Not tried: an experiment on the graph through the page (this project's connector had none), and zooming by hand.
- **Worksheets: no more weeks, and a New worksheet button.**
  - **New worksheet** (leader) opens a panel. Tick who it's for, then choose:
    - **Ask the Super Brain:** optionally say what it should cover. Left empty, it teaches what the others found, as before. With a request, people with nothing new are included too.
    - **Write it myself:** your own questions and "try it" steps, optional model answers and title, and a note for CREAO. CREAO writes the missing model answers, gives each item a topic and links the findings it relies on. Your title replaces CREAO's.
      - The saved worksheet is exactly your items, in your order, kinds and words. In a real run CREAO added 5 items of its own despite being told not to, so the app now matches CREAO's work to each of your items by the closest wording, keeps its topic, model answer (yours wins) and finding links, and drops anything it added. To have CREAO add questions, use Ask the Super Brain.

    Then **Send to CREAO**: one run writes one worksheet for each person picked.
  - **Numbered, not weekly:** each researcher's worksheets are Worksheet 1, 2, 3 in the order made. A cancelled one keeps its number on the leader's list, and that person's next worksheet takes the same number (the rule weeks had).
  - The week panel, week cards and **Cancel week** / **Restore week** are gone. One table lists all worksheets, newest first, each with Cancel or Restore.
  - Weeks cancelled before this become individually cancelled worksheets the next time the list loads, so each can be restored on its own.
  - The Super Brain is no longer told about weeks; it builds on "earlier worksheets".
  - **Model answers:** up to 2,500 characters (was 1,500). Longer text is now cut at the end of a sentence, or a word, with "…", never mid-word. A real model answer had ended "…a demonstrated one - th".
  - API:
    - `POST /worksheets` takes `request`, `title` and `questions`.
    - Worksheets carry `number` and a true/false `cancelled`.
    - `week` and `week_id` are gone from responses, and the `/worksheets/weeks/…` endpoints are removed.
    - No table change: the week tables now just hold each run's batch.
  - Checks:
    - New `tests/test_worksheet_making.py` replaces the weeks tests, covering: numbering and a cancelled number used again; asking with a request, even with nothing new; secrets removed from it; the leader's questions and title; bad questions refused; only-what's-new and correction notes; restoring one worksheet from an older cancelled week; week endpoints gone; worksheets older than batches numbered.
    - In Chrome, on a scratch backend running a copy of the demo data:
      - your 8 worksheets from cancelled weeks showed as cancelled, each with Restore;
      - **New worksheet** opened and both tabs filled in, without sending, so no credits were used;
      - a worksheet's page read "Worksheet 1 · For Researcher C";
      - there were no page errors.

      This caught the "Optional" hint dropping under its label, and an empty "For" list while it loaded. Both are fixed.
    - **With real CREAO** (the Super Brain hub agent), through the page, on a scratch copy of the demo data:
      - **Ask the Super Brain** for Researcher B, asking for the stability limit, about five items and two hands-on steps: 62 s, 5 items with 2 "try it" steps, topics, model answers and finding links.
      - **Write it myself** for Researcher A, two items: 59 s. Its title, items, order and model answer were kept, but CREAO added 5 more, which led to the fix above.
      - **After the fix**, three items for Researcher C (one no finding covers): 28 s, exactly the 3 items as written, the leader's model answer kept, CREAO's answers for the others, the uncovered one answered from general knowledge. No page errors.
    - New tests: a reply that adds items, puts its own first and rewords the leader's still saves exactly the leader's items, with the matching answers, topics and links; long text is cut at a sentence.
- **New dashboard design**, from the team's console mock-up, with ExpandRG's name, real data and existing features. The mock-up's sample content (made-up GPU hours, conflicts, an Agents tab) isn't copied, because nothing real is behind it.
  - **Sidebar:** the ExpandRG mark, a **project switcher** (the leader also gets **New project…**), navigation in groups, and the signed-in member at the bottom.
    - Researcher groups: Research (Project, Diagnose, Drafts), Knowledge (Super Brain), Simulation (Simulations, Visualize, Experiments). Leader groups: Knowledge (Super Brain), Group (Run results, Visualize, Projects, Offboarding).
    - Super Brain's tabs (Updates, Worksheets) and Simulations' tabs (Set up & run, Monitor) also show under them in the sidebar.
    - The drafts count and the red count of Critical problems in a live run stay.
  - **Top bar:** project / page breadcrumbs, the member chip and a **light/dark toggle**. The theme follows the system until you pick one, which this browser remembers.
  - **Member menu:** switch to another member in one click (prototype sign-in), or sign out. It replaces the old **Switch**, which went back to the sign-in page.
  - **Look:** soft surfaces, 1px borders, rounded cards with a header bar, segmented tabs, stat tiles, softer tables and badges. One indigo accent, Inter and JetBrains Mono (Google Fonts, system fonts offline), and Lucide icons (`lucide-react` 0.441.0, the dashboard's one new dependency).
    - Kept on purpose: a hypothesis is still dashed and muted, never green, and the agent's own labels still never look like a researcher's verification.
  - **Super Brain:** the "Ask Super Brain" chat is now a panel to the right of the updates, not docked at the bottom.
    - Your messages are on the right, its answers on the left. Instructions and Clear chat are buttons in its header, and the send button is an arrow.
    - Its left edge resizes its width. The old top-edge height handle is gone.
    - Worksheets use the full width, without the panel.
  - **Narrow windows:** below 1180 px the panel goes under the updates. Below 860 px the sidebar becomes a drawer behind a menu button, with the "built for laptops" notice.
  - **Sign-in** and the favicon match the new look.
  - Links like `?as=…&view=…&project=…&tab=worksheets` work as before. `view=alerts` is still Simulations for a researcher and Run results for the leader.
  - Docs: `Documents/dashboard-design.md` (shell, breakpoints, colours, decisions), `frontend/README.md` (its chat paragraph had drifted from the code and now matches it), and `Documents/demo-script.md` (the project switcher and the chat panel).
  - Checks: `npm run build` succeeds. Screenshots of the running dashboard in headless Chrome, at 1440 × 900 unless noted:
    - Researcher A: Super Brain in dark mode, then Diagnose, Simulations, Project, Drafts and Experiments in light mode;
    - the leader: Run results, Worksheets, Offboarding and Projects (dark mode);
    - sign-in, and the Super Brain at 820 px wide.

    The screenshots caught three problems, all fixed: the notice under the Offboarding title had turned grey, the sign-in list had an uneven bottom edge, and narrow screens would have hidden the member avatar.

- **ExpandRG installs as a package.** Using it meant a copy of this folder on each computer, with Node for the dashboard, and the extension pointed at that folder. Now it's one wheel, and the VS Code extension carries it:
  - **`expandrg` command** (`pyproject.toml`, new `expandrg/` package): `expandrg start [--group ID] [--no-browser] [--fresh] [--check]`, `stop`, `setup`, `add-project`, `version`. `run_demo.py` and `setup_expandrg.py` are now thin wrappers around it, with the same flags as before.
  - **No Node needed.** `expandrg/dashboard.py` serves the built dashboard and passes `/api` and `/connector` on, as the Vite dev server does. It uses the same address, http://127.0.0.1:5173. A checkout with `frontend/node_modules` still uses the Vite dev server.
  - **Files:** installed, the demo's data, its keys (`demo.env`) and the logs go in `~/.expandrg`, never inside the installed code. A checkout keeps `backend/data`, `backend/.env` and `.demo-logs` (`expandrg/paths.py`). The services run from `~/.expandrg`, so a project folder's own modules can't stand in for ExpandRG's. Messages name `expandrg …` or the scripts, whichever applies.
  - **`start` refuses while ExpandRG is already running** and says which group. Before, the old backend answered the health check, so a second start looked fine, but its backend couldn't take the port.
  - **`python build_package.py [--vsix]`** builds the dashboard into the package, then the wheel (`dist/`, copied into `vscode-extension/engine/`). With `--vsix` it also tests and packs the extension with the wheel inside. It refuses a wheel that contains `.env` files, databases, `data/`, `group.env` or `grants.json`. `backend/` got an `__init__.py` so it's packaged.
  - **Extension 0.14.0:** Set up no longer asks where ExpandRG's folder is.
    - It finds a Python with ExpandRG, or installs the wheel it carries into the first Python 3.10+ it finds or one you pick. pip's output is under Output → ExpandRG. Then setup continues; no second run is needed.
    - An older installed ExpandRG can be updated.
    - ExpandRG is started and stopped with `python -m expandrg`.
    - Workspaces already set up with a source folder (`expandrg.connectorFolder`) keep using that folder's `run_demo.py`.
    - What the launcher prints goes to `launcher.log`. If it stops without starting, the warning gives its reason at once instead of after 90 seconds.
    - A backend running without its connector is stopped before starting again.
  - Checks:
    - New `tests/test_package.py` (the command, file locations installed versus checkout, refusing to start twice, the dashboard's pages, forwarding unchanged, a readable error when a service is down). `tests/test_run_demo.py` covers the installed command's processes. Extension tests cover Python versions, the carried wheel, and the commands in both modes.
    - The wheel: 71 files, 242 KB with the new dashboard, with no keys, databases or `.env`. The `.vsix`: 15 files, 274 KB, carrying it.
    - Installed into a new virtual environment and run from a folder outside the repo:
      - the code loaded from the environment and its data went to `EXPANDRG_HOME`;
      - `expandrg start` refused because the demo was running;
      - `add-project` worked on a group made with the installed code;
      - the installed backend, connector and dashboard ran on spare ports (8110, 8111, 5273), and through the packaged dashboard: the pages and app script loaded; health, sign-in and project creation worked through `/api` (a duplicate refused with 422); a folder grant worked through `/connector` and was saved under `EXPANDRG_HOME`.
    - Not yet tried: the extension's Set up in a real VS Code window, and `expandrg start` on the default ports (the demo was running on them).
- `setup_expandrg.py` ends by pointing to **ExpandRG: Set up** instead of settings lines.
- **More projects in a group.** A group could only have the project it was set up with: setup made just that one, the dashboard had no way to add one, `--add-project` only worked on the demo, and VS Code's **Set up** lists projects from `group.json`, which nothing updated. Now the leader adds one either way, and both update the group's database and `group.json` together:
  - **Dashboard:** a leader-only **Projects** page lists the group's projects and has **New project** (a name, and a checkbox per active researcher, all ticked). The new project is then chosen in the top bar.
  - **Terminal:** `python setup_expandrg.py --add-project` asks which group (when there's more than one), the name (a name already used is asked again) and who works on it, and saves only after you confirm. Group setup now ends by mentioning it.
  - `POST /projects` takes the ID from the name when none is given, and in a group (`EXPANDRG_GROUP`) also writes `group.json`. Offboarded researchers are left out by default and refused when picked.
  - Answers piped into the setup from Windows PowerShell started with an invisible byte-order mark, which ended up in the saved name. It's removed now.
  - README: **Use it on your own research project** is rewritten around this. It used to say you work as Researcher A in the demo.
  - Checks: 5 new tests and one new refused case. In `tests/test_groups.py`: the terminal command with one group, several groups, no group and not confirming; offboarded researchers; the dashboard's API writing `group.json`. In `tests/test_project_overview.py`: the ID from the name, and a name with no letters refused. End to end on a scratch group, with your running ExpandRG left alone:
    - the real `setup_expandrg.py --add-project` added "Pipe flow" for Ben;
    - a scratch backend on port 8010 created "Jet flow" for Ana, refused the same name again (422) and refused a researcher (403);
    - the extension's own `listGroups` then gave Ana "Channel flow, Jet flow" and Ben "Channel flow, Pipe flow".

    The Projects page itself builds but wasn't clicked through in a browser.
- The install commands in `vscode-extension/README.md` and `Documents/agent-toolkit.md` named old packages (0.10.0 and 0.8.0); they now name 0.12.1.
- Final release checks: `python -m pytest`: **528 passed, 2 skipped**. Frontend production build and **34 extension tests** pass. Isolated Edge checks passed for Phase 1 (11 checks) and Phase 2 (12 checks), including a real experiment overlay, physical drag zoom, full-log restoration, exact differences/histograms/sweeps, and a 6.4 × 4.8 inch PDF. Two guarded requests through the existing CREAO connection verified a real heat-solver blow-up diagnosis/draft and graph refinement; all 11 referenced numeric claims matched. `python build_package.py --vsix` rebuilt 0.14.0; all runtime Python sources match the wheel, which includes Plotly and Phase 2, with identical copies in the extension and VSIX. No keys or databases are packaged. Installed-wheel smoke testing from outside the checkout also passed: all three services on spare ports, both API proxies, baseline-difference calculations, and a real publication PDF. Final layout checks passed for 24 researcher/leader page combinations at 1280px and 1440px.

---

## 2026-10-24 · `64e5c2a` · Guided first-time setup in VS Code, a resizable sidebar and talk box, Run results for the leader, clean agent replies, and weekly worksheets

**Why:** Someone new had to type their group, member ID and project into `.vscode/settings.json` by hand. Now VS Code asks.

- Extension 0.11.0: **ExpandRG: Set up** (a welcome message on first open, the bottom bar while it isn't set up, and a command). It asks:
  - for the ExpandRG folder, only if it isn't known;
  - for a Python that has ExpandRG's packages. It tries the configured one, a virtual environment in or next to ExpandRG, then PATH, and offers to install the packages into a picked Python;
  - for the group (those made with `setup_expandrg.py`, read from `~/.expandrg/groups/*/group.json`, plus the demo). **Set up a new group…** opens the leader's terminal setup;
  - who you are, and the project (only yours; one is chosen automatically).

  It saves the answers in the workspace settings, turns on terminal command reporting there, and starts that group. If another group is running, it asks before restarting ExpandRG with yours.
- Dashboard: drag the sidebar's right edge to make it wider or narrower (140 to 420 px), and the "Ask Super Brain" talk box's top edge to make it taller or shorter (120 px to 85% of the page).
  - Arrow keys work on a focused edge (16 px per press), and a double-click resets.
  - The browser remembers the sizes. A taller talk box fills with the conversation, and the input stays at the bottom.
  - Checked in a real browser on the running dashboard: dragged 200 to 300 px and 137 to 337 px, kept after a reload, stopped at 420 px, reset by a double-click, and 3 arrow presses added 48 px.
- **The leader's Monitor is now Run results.** It read like live surveillance, but it's the runs researchers shared: each one's log and data, drawn as graphs to see what happened.
  - The page says that in one sentence and lists "Shared runs, newest first", with researcher, date, steps recorded and problem counts.
  - Each run reads "Shared by … · date · N steps recorded", without Live/Finished tags, and has **Play back step by step**.
  - The red live count is gone from the leader's menu. Researchers' own Monitor tab is unchanged.
- **Stale "live" runs fixed.** A run counted as in progress forever when ExpandRG restarted without ending its watch: a run from 07:37 still showed "more of its log is still arriving", and researchers saw a stale badge of 5. Now a run is in progress only while data arrived in the last 5 minutes. This applies to the leader, the researcher Monitor and the menu badge.
  - Checked in a real browser on the running dashboard for the leader and Researcher A: the new wording shows, the newest run opens first, there are no stale Live tags or badge, and no page errors.
- **Clean, professional replies from the personal agent.** A real reply had four problems, all fixed:
  - **Raw Markdown in VS Code.** The chat panel showed `**`, backticks and `- ` literally. Extension 0.12.1 formats replies: paragraphs, headings, bold, italics, lists, inline code, code blocks and tables (`vscode-extension/src/markdown.ts`). They're built with `createElement` and text nodes only, so a reply can't inject markup. The `Sources:`, `Couldn't open:` and "Shared with your group" lines are small and grey.
  - **A leaked setup sentence** ("I'll start by reviewing the agent's skill instructions and manifest."). The filter now catches any opening first-person sentence about reading or reviewing skills, or the agent's or app's manifest. It still keeps sentences about the researcher's own manifest.
  - **A technical "Checked:" line** with raw errors and repeats. It's now `Sources:` (files by name, runs, Super Brain findings, no repeats) plus `Couldn't open:` for missing files. Per-request details stay in `tool_calls`.
  - **The agent couldn't find files.** VS Code had granted the whole workspace (`CALHACK_PROJECAT`), whose listing of 300+ files went over the size limit. So the agent guessed `config.json` at the top and failed.
    - Listings now leave out virtual environments (any folder with `pyvenv.cfg`), `site-packages` and hidden folders. `.demo-logs` alone held about 290 files.
    - More than 150 files come back as the folders that hold them; `list_project_files` takes `under` to list one folder.
    - A wrong path comes back with the files of that name.
    - The agent is told its own sandbox can't see the researcher's computer, to always look when asked to check files, to read by the exact listed path, and not to describe tool errors.
  - **Writing style:** the agent writes as a professional research colleague. It never mentions skills, manifests, tools, its process or ExpandRG's internals; it writes formulas in plain text, not LaTeX; it refers to nothing outside the reply (such as "H-01"); and it introduces itself in one short sentence. The "Shared with your group" line is shorter.
  - Checked with the real Agent 1 and the user's own VS Code folder, on a scratch backend. Before: no files read and a "can't open" answer. After: it read `config.json`, `run.log`, `heat2d.py` and `expandrg.json` in 3 CREAO runs and answered with the cause (r = 0.30 > 0.25, matched to the log), what isn't the cause, the exact change and a code guard, with clean sources. The panel was rendered with that reply and checked in a screenshot.
  - New tests: Markdown parsing and safe rendering (including HTML staying text, and snake_case untouched); the sources line; big folders, `under`, wrong-path suggestions and virtual environments; and the new setup-sentence wording.
- **Weekly worksheets.** Each "Make worksheets" is the project's next week.
  - **Only what's new:** each researcher gets only findings that are new to them, shared or given a note after their last worksheet that wasn't cancelled. The agent is told the topics they already covered, to build on them, and that a correction note overrides a finding. Only those findings are sent, and "only what's new" can be turned off.
  - **Optional per person:** the leader ticks who gets one. A preview (`GET /worksheets/next`) shows each person's count of new findings and greys out anyone with nothing new.
  - **Cancel and restore**, either a whole week or one person's worksheet. Cancelled worksheets disappear for the researcher (and refuse answers). The cancelled lessons come back in that person's next week.
    - **A cancelled week's number is used again.** Cancelling week 1 made the next one week 2. Now weeks are numbered by those that weren't cancelled: the panel goes back to **Week 1** as soon as it's cancelled (it now reloads on cancel and restore), and the cancelled week stays listed as Week 1, tagged Cancelled. Restoring it moves the later weeks up by one. No table change: `worksheet_weeks.week` keeps the order weeks were made in, and the number shown is worked out from it.
  - **Storage:** new tables `worksheet_weeks` and `worksheet_in_week`. The layout version stays the same, so existing databases keep working. Worksheets made before weeks existed become week 1; on this computer, the 3 existing ones did.
  - **Page:** a **Week N** panel with the tick-list, one card per week with **Cancel week** / **Restore week**, and a per-person **Cancel** / **Restore**.
  - **Checks:**
    - 9 new tests in `tests/test_worksheet_weeks.py`: numbering and nothing-new refusal; only what's new plus covered topics; a correction making a finding new; everything again; cancelling a week (hidden, answers refused, lessons back, restore); a cancelled week's number used again (it stays listed, and restoring renumbers); cancelling one person; leader only; earlier worksheets becoming week 1.
    - A real-browser check with two weeks and one person cancelled: the plan showed Week 3 with only Researcher B having something new. Clicking **Cancel week** on Week 2 greyed it with **Restore week**. Researcher A then saw only Week 1, and Researcher B saw nothing. No page errors.
- `run_demo.py --stop` stops a running ExpandRG wherever it was started: the launchers, backend, connector and this folder's dashboard, never another app's dev server or itself.
- `GET /health` now says which group the backend serves (`group`: an ID, or `demo`).
- `setup_expandrg.py` ends by pointing to **ExpandRG: Set up** instead of settings lines.
- Checks:
  - New `tests/test_run_demo.py` (the stop command picks only ExpandRG's processes; `/health` reports the group) and `vscode-extension/src/test/setup.test.ts` (groups listed from disk, a broken group file skipped, projects per person, `EXPANDRG_HOME`, the ExpandRG folder check, Python candidates). The group test checks the new closing message.
  - A dry run of `--stop` on this computer picked exactly ExpandRG's 7 processes out of 185. Then `--stop` for real stopped ExpandRG, it started again, and `/health` answered `{"status": "ok", "group": "demo"}`.
  - `python -m pytest`: 470 passed, 2 skipped. `npm run build` succeeds. `npm test` in `vscode-extension/`: 31 passed. Extension 0.12.1 packaged and installed.

---

## 2026-10-24 · `407964b` · Agent toolkit, Super Brain worksheets and comments, your own research group, and simulations from the dashboard

**Why:** Personal agents should look at the researcher's files, runs and the group's findings before answering. The Super Brain should teach and be easy to read, and group members should be able to comment on what's shared. A real group needs its own Super Brain and agents rather than the demo. Researchers should be able to set up and run an existing simulation from the dashboard, with their agent preparing the steps and the project's own environment and compilers reused.

### A read-only toolkit for personal agents, worksheets, the update board and group setup

**Why:** When a researcher asks about a blow-up, their agent should look at the code, the config, the log and the group's findings instead of guessing. The plan was MCP tools, so CREAO's support for them was checked first. And the group wanted the Super Brain to teach: take everything shared at once, organize it, and give each researcher a worksheet the leader can check.

#### Worksheets (the Super Brain as a teacher)
- Decisions: questions and things to try; one worksheet per researcher, from what the *others* found; the leader makes them; researchers answer and the leader comments (no automatic grading).
- `backend/knowledge_sharing/teacher.py` and `POST /worksheets` (leader), `GET /worksheets`, `GET /worksheets/{id}`, `PUT /worksheets/{id}/answers`, `POST /worksheets/{id}/review`, `DELETE /worksheets/{id}`.
  - One run on the Super Brain agent reads every finding shared in the project and writes all the worksheets.
  - The app keeps only the planned researchers, at most 8 question or "try it" items each, and finding IDs from what that researcher may learn from.
  - Model answers stay hidden from the researcher until the leader has reviewed the worksheet; answers lock after review.
  - New shared tables `worksheets` and `worksheet_items`, created on start.
- The code that picks the Super Brain agent (dedicated chat agent or hub) is now one helper, shared by chat and worksheets.
- **Update board, easier to read.** Each update on the Super Brain page shows:
  - a plain name (the title without numbers and formulas);
  - the **Issue** and the **Likely cause**, taken from the summary's labelled parts, or else the agent's guess. They sit in one plain label column: a first version with coloured boxes was too loud;
  - the graph of the run it came from, with its Warning and Critical marks;
  - **Comments**.

  Personal agents are asked for share titles without numbers or formulas.
- **The run a finding came from.** New shared table `finding_runs`. Drafts offer **Attach a run** (a run started up to a day before is already selected). `run_id` on approval must be your own run in that project. A personal agent's chat share carries the run it looked at, if it's the researcher's own and in that project. Runs stay private: the graph shows only to those who may see the run (the researcher and the leader).
- **Comments** (`finding_comments`, `GET`/`POST /findings/{id}/comments`): one thread per finding that the leader and the project's researchers all write in and see, also on the finding's own page. Agents don't read comments; notes remain the way to add detail they use.
- Comments that wouldn't post: the running backend dated from 10:36, before comments existed, while the dashboard had already reloaded with them. ExpandRG was restarted with current code (data kept). A real browser on a scratch copy then opened an update's comments, typed, clicked **Post** and saw the comment appear, with the box cleared and no error.
- Checks: 5 new tests in `tests/test_update_board.py` (an attached run shows for those who may see it; only your own run in the project can be attached; a chat share carries the run the agent looked at, never another researcher's; a shared comment thread; comments follow project access, are checked and have secrets blanked). The text helpers were checked on real summary shapes in Node. Screenshots: the leader's board with two runs, Researcher B seeing only their own run's graph, and Drafts with the run already selected.
- **Set up your own research group** (`python setup_expandrg.py`, `backend/workspaces/groups.py`).
  - Decisions: the setup runs in the terminal; a new group gets its own empty Super Brain and the demo stays; the leader enters every researcher's agent.
  - It asks whether you're the leader (researchers are sent to their leader), then the group, the Super Brain's app ID and key with where to find them in agent.creao.ai, each researcher's name, app ID and key, and the first project.
  - Keys are typed hidden, never printed in full, and checked with CREAO at no cost. Checked on 2026-10-24: a wrong key gets 401, but CREAO answers any app ID, even a made-up one. So app IDs are checked for shape, with an optional one-run test that asks first because it uses credit (an unknown app gets 404 `APP_NOT_FOUND`).
  - It saves `~/.expandrg/groups/<id>/group.env` (keys), `group.json` (no keys) and `data/` (a new Super Brain with the people, their agent slots and the project).
  - `run_demo.py --group <id>` starts it and prints each person's sign-in link. The CREAO client reads `EXPANDRG_ENV_FILE` when set. The demo is never added to another group's database.
  - VS Code extension 0.9.0 has an `expandrg.group` setting, so it starts that group instead of the demo. Its README links the toolkit doc on GitHub, because vsce refuses links outside the extension.
- Checks for groups: 9 new tests in `tests/test_groups.py` (a full leader setup; a researcher sent to their leader; bad IDs and keys asked again; project members and unique IDs; an existing name refused; not saving saves nothing; a started group uses its own data and keys, never the demo's; the demo is never added; key and app checks). A real run used the real keys (accepted for free) in a scratch folder: the group's own backend listed only its people, and Ana's agent answered through the group's keys (22 s). The scratch copy of the keys was deleted.
- Super Brain chat shows only the question and the answer: the "Answered in … · N shared findings available" line and the "Shared knowledge" list are gone, and so are the retrieved-findings lines while it's thinking. They're still saved with each exchange.
- Dashboard: **Updates** and **Worksheets** tabs on the Super Brain page.
  - Leader: **Make worksheets** with a live timer, a table with each status, and per-item comment boxes (**Send comments**, **Delete**).
  - Researchers: items grouped by topic with links to their findings, answer boxes, **Save** and **Send to the leader**, then the leader's comments and the model answers.
  - `?tab=worksheets&worksheet=<id>` links straight to one.
- Checks:
  - 6 new tests in `tests/test_worksheets.py`: what each researcher may learn from; unknown, duplicate and malformed worksheets and items dropped, and the cap; leader only; nothing to teach; only the project's findings; who sees what and when model answers appear; answer, submit, review and lock; delete; a reply without worksheets.
  - A real run on the hub with the user's Super Brain Chat skill (`04bce97c`, about 60 s) wrote 3 worksheets (7, 7 and 8 items) for Researchers A, B and C from two findings. Each item linked the right finding, A's own finding was never taught to A, and the verification labels were kept honest.
  - Screenshots at 1280 and 1440 wide checked the leader's list and checking view, a researcher answering, and a reviewed worksheet; a wrapping table was fixed.

#### CREAO check
- API-triggered agents can't call custom tools. Agent 1, asked directly (run `a8d5e5ca`), listed only CREAO's own tools and `connectors: []`. CREAO's custom MCP servers are for the build agent; Device Connect has no documented Windows or API-run support and would give shell and write access; webhooks to private networks are rejected.
- The same run showed the agents can run code in CREAO's cloud (Python, outbound HTTP). Agents are now told never to run project code there; the design doc's risk 1 is updated.

#### Changed
- `local_connector/agent_tools.py` and connector endpoints `POST /tools/list_project_files` and `POST /tools/read_project_file`:
  - a granted folder named by its name only (`GrantRegistry.grant_named`; two folders with the same name are refused);
  - research file types only; relative paths only;
  - line ranges with line numbers, and logs excerpted around problems;
  - 400 lines and 12,000 characters per read; secrets blanked. Nothing is executed.
- `backend/orchestration/toolkit.py`: `list_project_files`, `read_project_file`, `search_super_brain` (findings in the researcher's projects, with evidence, status and notes) and `get_simulation_runs` (their runs, alerts and numbers per step). All read-only, run with the researcher's permissions.
  - Requests that are unknown, refused or over the limits come back to the agent as errors.
  - `POST /agent-tools` runs one tool directly (for an MCP server later).
- Personal chat: the agent ends a reply with `{"tool_requests": [...]}`. ExpandRG runs up to 5 requests per round, 2 rounds per message, and calls the agent again with `tool_results`. The reply ends with a `Checked:` line; history keeps the summaries, not the file contents. `POST /personal-agent/chat` takes an optional `folder` (a name, never a path).
- After the first real run answered without looking, the prompt now says the connected folder is readable, and `output_format` asks for either only the tool requests or the final answer.
- The skill-preamble filter also removes "I'll start by reading the app manifest and skill."
- **Fixed:** listing a granted folder followed Windows directory junctions, so names of files outside the folder could appear in the dashboard's file list and in a project map sent to CREAO. Reading them was already refused. Linked folders and links that lead outside are now skipped (`local_connector/file_access/walk.py`).
- VS Code extension 0.8.0: the panel sends the granted folder's name with each message and shows `· can read <folder>` in its header. On the first message, a single open folder that isn't allowed yet is offered once.
- Docs: new `Documents/agent-toolkit.md` (tools, rules, the CREAO check, Windows and VS Code setup, the real demo); backend README, top-level README, the extension README and the runtime design updated.

#### Checks
- New `tests/test_agent_toolkit.py` (29 tests, 1 skipped where symlinks need admin rights). It covers:
  - only research files listed;
  - ranges and log excerpts; caps;
  - refusals for `..`, local paths, `.env`, keys, `.git`, binary and unknown types; Windows junctions, for both listing and reading; folders that weren't granted or share a name;
  - secrets blanked; reading never runs or changes anything;
  - the chat loop end to end, the 2-round and 5-request caps, the size limit; refused and unknown requests;
  - chat without a folder; a stopped connector;
  - search with project, evidence, status and notes; a restricted project and another group never searched; search read-only;
  - runs only the researcher's own; the endpoint's sign-in, membership and folder-name checks.
- `python -m pytest` (with the worksheet tests): 391 passed, 2 skipped. `npm run build` succeeds. `npm test` in `vscode-extension/`: 20 passed. Extension 0.8.0 packaged and installed.
- Real Agent 1 on scratch data (test ports 8100/8101):
  - it listed the folder, searched the Super Brain, checked runs and read `config.json`, `heat2d.py`, `run.log` and `expandrg.json` in 2 rounds (3 CREAO runs, 72 s in CREAO);
  - it answered with r = 0.30 from the config and `heat2d.py` line 32, the growth factor from the log, and Researcher A's verified fix.

### Keep group setup short and guided

- The terminal introduction now says setup connects agents already created in CREAO. It asks whether the user is the leader before collecting names or credentials, and retries an unclear role answer.
- Researchers are told to wait for their leader's group ID and sign-in name/member ID; that branch exits without credential prompts, key checks, or group creation.
- Leaders see app ID/key directions at entry: open the existing agent, open API Trigger, and copy its app ID and `capi_` key. Keys remain hidden when typed. Wording distinguishes the new group's empty local research memory from the existing CREAO agents.
- Checks: all 10 group-setup tests passed with fake credentials and no live CREAO calls.

### Replace the run shortcut with simulation setup guidance

- Extension 0.10.0 replaces **Run a simulation** with **Set up simulation** in the status bar and command palette. The action opens the personal agent with a setup request ready to review and send; the old manifest-based process launcher was removed.
- The request asks the existing CREAO personal agent to inspect permitted project files and identify the real language, environment, build system, dependencies, inputs, working directory, and run commands. Python guidance covers its environment and entry point; compiled languages such as Fortran get separate build/link and execution steps. The researcher starts commands in their normal terminal.
- The chat shortcut uses the same request. Existing unsent messages are preserved, a request survives the webview's initial load, and switching profiles discards a queued setup request. Terminal monitoring and explicit log watching remain available.
- Checks: TypeScript compiles; all 22 extension tests pass. Browser checks confirm draft preservation, explicit Send, and the existing personal-chat interactions at narrow/light/dark sizes. No live CREAO run was submitted for this UI change.

### Prepare and run simulations from the researcher dashboard

- Replaced the researcher's manual log picker and **Start watching** flow with **Simulations → Set up & run** and a separate **Monitor** tab. The leader sees Monitor without execution controls.
- **Detect & prepare** saves a code/input version and asks the researcher's existing CREAO agent for a structured setup based on a checked project map and local tool availability. It identifies environments, build dependencies, inputs, and execution commands; unresolved choices or missing tools block Run. No hand-written run manifest is required.
- **Run simulation** registers monitoring, makes a fresh work copy, executes build steps sequentially, and runs the simulation only after successful builds. Output streams into the existing metrics/alert pipeline automatically. Run and Stop are explicit researcher actions. Windows compiler names and project executable paths are handled separately from global tools.
- Local saved versions retain code, inputs, setup, output, and results. **Prepare from this version** reuses a prior snapshot and setup without another CREAO request. Original project files remain unchanged; sensitive files and symlinks are omitted. Versions remain separate between owners and groups.
- Added authenticated connector simulation routes and `POST /simulation-setup` on the backend. Setup jobs reconnect without automatic resubmission. Execution uses the existing time/memory/output limits; it is not filesystem or network isolation.
- Checks: full backend/connector suite passed (421 tests, 2 skipped); final simulation/source-boundary checks passed (20 tests). Frontend production build passes. An isolated browser completed real Python build/run, automatic metrics, version reuse, reload persistence, missing-Fortran-compiler handling, and leader monitoring. A real Agent 1 CREAO request also produced a validated runnable Python setup in 31.5 seconds; the request asked for planning only.

### Reuse the project's own environment and compilers

- `local_connector/runtime_environment.py`: **Detect & prepare** looks for the project's `.venv`, `venv` or `env`, then an activated virtual or Conda environment, then ExpandRG's own Python. That interpreter is used both to check the project's packages and to run it. Saved versions keep it; the environment isn't copied or recreated.
- Compiler settings are reused from `FC`, `CC`, `CXX`, Makefiles and CMake presets or caches, including compilers outside ExpandRG's PATH. Plans name them only through the `{python}`, `{fortran}`, `{cc}` and `{cxx}` aliases.
- Local interpreter and compiler paths never go to CREAO: it sees labels, availability and aliases. A plan can't add its own absolute path.
- **Existing environment** (before preparing) and **Environment details & connection** (on a saved version) let the researcher point to an environment folder, a Python executable or a Fortran compiler. **Recheck existing tools** refreshes detection without another CREAO request. Credentials in the launcher's environment are never saved with a version.
- SDKs that need activation scripts must be activated in the terminal that starts ExpandRG. Windows and WSL are separate environments.
- New `tests/test_runtime_environment.py` (about 25 tests) covers the order of choice, explicit selections, Conda, compiler aliases with their flags and quoting, Makefile compilers, missing tools, and keeping local paths and credentials private.
- Docs: README, backend README and the runtime design now describe Set up & run, Monitor and environment reuse. This part was written before its own changelog entry; it is recorded here.

### Checks for this commit

- `python -m pytest`: 452 passed, 2 skipped (symlink tests need admin rights on Windows). `npm run build` succeeds. `npm test` in `vscode-extension/`: 22 passed.
- ExpandRG was restarted with this code: the backend has `/simulation-setup`, the comment, worksheet and agent-tool routes. Extension 0.10.0 was rebuilt from this source and installed.
- Secret scan of the staged changes: no CREAO key or app ID from `backend/.env`; no `.env`, database, log or package files are committed.

---
## 2026-10-24 · `79bf13c` · Agent runtime Phase 1, the VS Code extension with personal agent chat, and chat sharing to the Super Brain

**Why:** Personal agents should be able to investigate problems on their own, but they must never start a run. The design for that is in `Documents/agent-runtime-design.md`. Phase 1 builds its base without any AI: a researcher tries a change in a copy of the project, runs it with a click, and compares it with the original run. Researchers' code lives in VS Code, so their agent should be there too: watching runs and talking with them, and passing what matters to the web dashboard and the Super Brain. The Runs page also had to become clear ("I don't know what I'm looking at").

**Design**
- Added `Documents/agent-runtime-design.md`: the local agent runtime design. It covers the principles, the architecture, the six decisions, the investigation flow, the experiment environment, checks and labels, privacy, CREAO usage, the Super Brain and the leader, the VS Code extension, the risks and the build order.
  - Agents never start a run; every run waits for **Run**.
  - CREAO only reasons; everything runs locally.
  - Changed later the same day: what a personal agent establishes in chat it shares itself, as a hypothesis (principle 6, decision 5).

**Experiments (Phase 1)**
- Run manifest (`expandrg.json` in the project folder, `local_connector/experiments/manifest.py`): the commands experiments may run (`{python}` is ExpandRG's Python), the JSON config keys they may change, and per-run time, memory and output limits (capped). Bad manifests are explained. The sample projects have one.
- Experiment copies (`local_connector/experiments/workspace.py`):
  - the project is copied under `~/.expandrg/experiments/<id>/`, without `.git`, `.env`, keys or tool folders, and copies over 500 MB are refused;
  - only the manifest's config keys can change, and only to values of the same type.

  The project folder is only ever read.
- Process sandbox (`local_connector/experiments/sandbox.py`):
  - time, memory and output limits, and a stop button;
  - on Windows a Job Object caps memory, and stopping a run ends everything it started; elsewhere, resource limits and a process group;
  - keys and tokens are removed from the run's environment.
- Experiment manager (`local_connector/experiments/manager.py`):
  - **Prepare** makes the copy and runs nothing;
  - **Run** / **Run all** queue experiments, which run one at a time;
  - **Stop** cancels a queued or running one;
  - results are summarized from the log with the existing parsers (numbers per step, alerts, last step);
  - experiments left running when the connector stopped are marked stopped.
- Connector endpoints: `POST /manifest`, `POST /experiments`, `GET /experiments`, `GET /experiments/{id}` (with the baseline log's summary), `POST /experiments/run`, `POST /experiments/{id}/stop`, `DELETE /experiments/{id}`.
- Dashboard **Experiments** page (researchers):
  - a new comparison: the folder, "Compare with" (a log), and up to three experiments as parameter rows;
  - one card per comparison with **Run**, **Run all** (showing the total time limit), **Stop** and **Delete**;
  - comparison charts: the original in gray, experiments in blue, orange and green (validated all-pairs; the light green's sub-3:1 contrast is covered by the legend and the table), a shared legend, and a hover readout of every run at a step;
  - a table with each run's result and alerts, last step, final values and first Warning and Critical steps.

**Runs page, in plain words**
- One sentence on what it watches: the log file the simulation writes, which ExpandRG only reads.
- A "How to use it" box with the three steps and an example command, open until the first run exists.
- A guide to what Warning and Critical mean (shown to the leader too).
- Plain labels: "Log file to watch", "Being watched now" / "Finished", "Watching now", "Latest problems found", "No problems found so far"; one line explaining the graphs.

**Streamed runs**
- `local_connector/simulation_monitor/stream.py`: the connector accepts a run's output line by line, from a VS Code terminal, instead of only a log file. It applies the same checks and reporting, and a non-zero exit code is a Critical problem (`exit_code`).
  - The command line becomes the run's name, with secrets blanked.
  - Only granted folders can stream.
  - Endpoints: `POST /streams`, `POST /streams/{id}/lines`, `POST /streams/{id}/end`, `GET /streams`.

**VS Code extension (`vscode-extension/`, TypeScript, 0.2.0 to 0.7.4)**
- Status bar showing what is watched and its Critical problems (red when there are any), which opens the dashboard. Commands to watch a log file (asking once to allow the folder), stop watching, choose the project and open the dashboard.
- Runs started in the VS Code terminal (`src/runs.ts`), as decided in the design:
  - a command running an `expandrg.json` entry point is watched automatically;
  - other commands that look like runs (`python`, `./program`, `mpirun`, `julia`, `Rscript`) ask "Watch this run?";
  - everything else is ignored, and its output is never read;
  - output going to a file (`> live.log`, `| tee`) is watched through that file; otherwise the terminal's output is streamed line by line, with colour codes removed;
  - typed `cd X; python ...` and `cd X && ./solver` count as the run in folder X;
  - pop-ups for the first Warning and Critical, and at the end (with the exit code of a failed run), each with **Open dashboard**;
  - the folder permission covers subfolders of an allowed folder; setting `expandrg.watchTerminalRuns`.
- After the first real try in VS Code (command reporting was turned off in the user settings, `run_demo.py` in a VS Code terminal stopped when the window reloaded, and lines pasted with a trailing `` ` `` joined into one broken command):
  - **▶ Run a simulation** (bottom-bar button and command): the researcher picks an `expandrg.json` entry point and the extension starts the process itself, in the project folder (`{python}` becomes `expandrg.pythonPath`, unbuffered). Its output shows in a terminal named "ExpandRG: <name>" while it's watched, whatever the terminal settings; closing that terminal stops the run. The researcher starts the run, so the no-automatic-runs rule holds.
  - ExpandRG starts in the background (`run_demo.py --no-browser`, detached) when `expandrg.connectorFolder` is set and it isn't running, so reloading or closing VS Code doesn't stop it. When it isn't running, a warning offers **Start ExpandRG**, and a pop-up says when it's connected.
  - The **ExpandRG** output panel logs what the extension saw: start-up, whether ExpandRG is reachable, each terminal command (watched, ignored or asked about, and why) and each run's result.
  - Fixed: the last lines of a run could be lost when it ended while a batch was still being sent.
- Personal agent chat beside the code (0.7.0 to 0.7.4), named **ExpandRG AI**, with a visible **Powered by CREAO** note:
  - **My agent** and **Talk to my personal agent** open private chat; each researcher uses their own CREAO agent and its saved model;
  - **Understand project** reads a granted folder locally and shows the exact project-map payload; **Send to my agent** explicitly starts the overview workflow, saved for later conversations; **Discuss simulation setup** prepares a question on compilers, build systems, libraries, parameters and output, and sending stays explicit;
  - the panel shows the assigned **Agent 1**, **Agent 2** in its tab, header, welcome and reply labels;
  - its own slate-and-teal style, sans-serif type and a text wordmark; instructions behind a settings button; project previews open the context panel, and Escape closes it;
  - private state resets when the member, project or backend changes, late replies from an old identity are ignored, and pending replies reconnect; text is rendered as plain text under a nonce-based content policy, and tokens stay in the extension host;
  - a missing personal-chat route explains that ExpandRG must be restarted (it was an older backend still running).
- `vscode-extension/README.md`: what you see (with a sketch), commands, workspace settings, install and "If nothing happens".
- `run_demo.py --no-browser` starts everything without opening dashboard windows (the extension uses it).

**Personal agent chat (backend)**
- `POST /personal-agent/chat`, `GET` / `DELETE /projects/{id}/personal-chat`: private history, separate from Super Brain conversations. Context is the saved project overview, recent conversation, the researcher's instructions and the project's shared findings. Leaders have no personal agent and are refused; one reply per researcher and project at a time.
- Agents are numbered by registration/activation order in the workspace (`/me` returns the identity), independent of CREAO credential names; retired agents keep their place. Each request names the agent and asks for a short introduction in a new conversation.
- Replies answer the question directly, in short paragraphs for simple questions. A narrow filter removes a leaked opening sentence about reading internal skill files (including "the agent's skill and manifest"), and keeps explanations about skills, quotes and statements about missing access. "Be conversational and answer directly" saves through the rules workflow.

**Personal agents share chat findings; members add detail and guesses**
- Checked first: neither chat could pass anything to the Super Brain (0 drafts and 0 findings before and after a chat). The group chose that each personal agent shares what it establishes in chat itself, while anyone can add more detail or their own guess when the agent didn't cover it well enough.
- The personal agent ends each reply with a share block (title, summary, category, evidence, its own guess), or `null` for greetings and general answers. The app removes the block, publishes a usable one at once as a **hypothesis** in that project, and adds a plain line to the reply naming what was shared. An agent can't mark its share verified. The same title from the same researcher isn't shared twice, secrets are blanked, and a researcher removed mid-reply keeps the reply but shares nothing. Diagnoses still need approval.
- New shared tables `auto_shares` (which findings an agent shared, and its guess) and `finding_notes` (detail, guesses and corrections), created on start; existing databases keep working. `POST /findings/{id}/notes` adds a note to a finding you can read; `GET /findings/{id}` returns the notes. Findings carry `auto_shared` and `agent_guess`.
- The Super Brain and every personal agent receive each finding's notes and the agent's guess, marked as shared by an agent, so corrections reach their next answers.
- Dashboard Super Brain: a "shared by their agent" tag, "The agent's own guess · not evidence", and "Added by the group" with the notes and a form (More detail / My own guess / Correction).

**Docs and tests**
- Updated `README.md`, `backend/README.md` ("Sharing from chat", personal chat, endpoints, tables), `Documents/dashboard-design.md`, `Documents/demo-script.md` and the connector's description.
- `tests/test_definition_of_done.py`: the connector may now write its experiment copies and logs under `~/.expandrg/experiments`, besides its grants file. The project folder staying unchanged is checked directly in the new tests.

**Checks**
- `python -m pytest`: 356 passed, 1 skipped (symlink test on Windows). `npm run build` succeeds. `npm test` in `vscode-extension/`: compiles, 19 tests pass.
- New tests:
  - `tests/test_experiments.py` (24): manifest checks; copies leaving out secrets and tool folders, with the project byte-for-byte unchanged after preparing and after running; preparing never running anything; the time, output and memory limits and the stop button (including a process the run started); no keys or tokens in the run's environment; a real heat-solver experiment; Run all one at a time; restart recovery; the service end to end.
  - `tests/test_streams.py` (5): streamed lines get the same checks as a log file; a failing exit code is Critical and ends the run; a terminal run reaches the researcher and the leader but not other researchers, with a secret in the command blanked; only granted folders can stream.
  - `tests/test_personal_chat.py`: identity, privacy from other members and hub chat, rules in chat, pending replies, failures, redaction, and the skill-preamble filter.
  - `tests/test_chat_sharing.py` (15): a share block becomes a hypothesis finding without a draft; unusable blocks share nothing; no forced "verified"; no duplicate titles; secrets blanked; shares stay in their project; notes and who may add them; the Super Brain and other agents see the guess and the notes.
- The extension, with a stand-in for VS Code against a real connector and backend:
  - terminal runs: the `expandrg.json` command was watched automatically (Warning and Critical pop-ups, "ended with exit code 1"; on the dashboard to step 2223); another run asked first; `git status` was ignored and its output never read; `> live.log` was watched through the file;
  - **Run a simulation**, three times after the fix: the terminal showed the solver's output, the pop-ups appeared, and each run reached the dashboard to step 2223 with 6 Critical problems.
- Installed in the user's VS Code from the `.vsix` (now 0.7.4). The extension's own log confirmed it started, connected, and could not see typed commands while command reporting was off.
- Experiments end to end on scratch data, as Researcher A on test ports: dt = 0.02, 0.025 and 0.028 stayed "prepared" until **Run all**, then ran one at a time. 0.02 and 0.025 stayed stable for 3000 steps; 0.028 diverged (Critical at step 1300); the original blew up at step 2223. The project folder was unchanged. Charts, legend, hover and table were checked in light and dark.
- The Runs page as a new researcher sees it, on fresh scratch data, following the three steps with the real solver; the leader's view too.
- Real Agent 1 runs:
  - personal chat request: 32.1 s, saved to private history;
  - two conversational replies without skill-file preambles or report headings;
  - on a scratch database: a greeting shared nothing (28 s); a dt-bracket result was shared at once as a hypothesis with the agent's guess kept apart, and Researcher B saw it (38 s).
- Secret scan: no API key or app ID in the commit.

---

## 2026-10-24 · `c9bf423` · Runs page, Super Brain page with CREAO chat, and project-map fixes

**Why:** The dashboard was too complicated. The leader needs one clear page of what each researcher has shared, and a way to ask the group's knowledge for more detail. Simulations should be watched live on the dashboard, with their alerts, and old runs opened again later. The first real-project test of the project overview (build plan step 8) also showed three gaps in the project map.

**Changed**
- Layout:
  - the project picker is in the top bar on every page, and every page works on the chosen project;
  - the right-hand alerts panel is gone (alerts now live with their run);
  - the window doesn't scroll as a whole: only the main view scrolls;
  - every page uses the same width;
  - card headers align on the text baseline.
- **Runs** page (the nav item formerly called Alerts), for researchers and the leader:
  - A researcher picks a log from their granted folders (the list refreshes itself) and clicks **Start watching**. The local connector reads the log and sends the numbers from each step line (e.g. `step 200  max|T| = 2.75e+14  residual = 4.7e+14`) and the alerts, never the log file. The run appears on the dashboard at once.
  - On the left, the project's runs: **Live** first, then **Earlier runs**. On the right, the chosen run:
    - the newest alerts under its header;
    - up to 3 line charts, one per measure, on a shared step axis. They switch to a log scale for values spanning 1,000x or more. Warning and Critical alerts are marked at their step, and hovering (or arrow keys) shows the value at that step in every chart;
    - all alerts, and the numbers as a table.
  - **Stop watching** ends a live run. **Replay** plays an earlier run back step by step in about 8 seconds. A new run written into the same log becomes a new run.
  - The Runs tab shows a count of Critical alerts in runs being watched right now.
  - Runs are seen by the researcher and the group leader, never by other researchers. The "Save alert history" switch is no longer in the dashboard (its API remains); runs keep their alerts instead.
  - Backend:
    - new shared tables `simulation_runs`, `run_points` and `run_alerts`;
    - `POST /simulation-runs`, `POST /simulation-runs/{id}/report`, `GET /simulation-runs`, `GET /simulation-runs/{id}`, `GET /simulation-runs/alerts`;
    - a run keeps up to 6 metrics, and past 1,000 points per metric every other point is dropped, keeping the last one and every NaN/Inf point;
    - alerts record their step;
    - `watch` on the command line also sends the run.
- **Super Brain** page:
  - four counts: updates shared, researchers sharing, verified, still hypotheses;
  - a bar chart of updates per researcher (one colour, square ends, value at the tip, hover for the status breakdown, click to jump to that researcher);
  - each researcher's updates: title, verification tag, the issue (the summary's "Symptom" line), category and date;
  - `GET /superbrain/findings` lists approved findings across your projects (optional `project_id`).
- **Ask Super Brain** chat, docked at the bottom of the Super Brain page:
  - Every message is a CREAO run. Real runs showed the time goes to the hub's audit workflow (21 s for "hello", 35 to 46 s for questions), not to the app. So chat:
    - runs on a dedicated chat agent once `CREAO_CHAT_APP_ID` and `CREAO_CHAT_KEY` are set. It's an ordinary CREAO agent with an app key (`capi_`), only `question` and `context` inputs and no tools. Until then it runs on the hub (`CREAO_HUB`), which is only used for chat and can instead be slimmed down in CREAO itself. `Documents/super-brain-chat-agent.md` gives both setups;
    - asks the hub for a direct answer without its audit, tools or skills;
    - asks CREAO for a fast model (`CREAO_CHAT_MODEL_ID`; empty means `gemini-3-flash`, `saved` keeps the agent's own).
  - Diagnoses and project overviews still use each researcher's own agent and its saved model.
  - `POST /superbrain/chat` (alias `/agent/chat`) retrieves the approved findings the member may read: complete summaries with author, project, verification label and source run. The hub combines them with its own scientific knowledge, keeping observations apart from theory and citing the findings it used.
  - Knowledge scope: the chosen project (default) or all your projects. Recent conversation is included. The last 12 exchanges are kept in private settings, and `GET`/`DELETE /projects/{id}/chat` show and clear them.
  - **Instructions** (how the agent should answer; `GET`/`PUT /projects/{id}/agent-rules`) are saved per user and project and also apply to diagnoses and project overviews. They can be set in chat ("Act as a researcher and be clear", `/rules`, `/rules reset`).
  - Private project overviews are never sent to the hub. Personal diagnoses and overviews still use each researcher's own agent. Every message uses CREAO credits.
  - The box is one compact row aligned with the page.
- Diagnose and Project: folders are shown by name (full path on hover), with **Add a folder** to grant another.
- Project map (`local_connector/evidence/project_map.py`):
  - other guides besides a README are picked (`.md`, `.txt` and `.rst` notes such as how-to-run files);
  - folders of up to 40 files are listed in full;
  - up to 10 code outlines are included, main scripts first, then parameter files.
- Overview check (`backend/orchestration/overview.py`): the project's own folder counts as part of the project, so the agent naming it isn't marked as invented.
- Added `Documents/benchmarks/hub-chat-speed-2026-10-24.md` (and its JSON): a historical comparison of hub request setups. That faster setup is no longer active.
- Updated `README.md`, `backend/README.md`, `frontend/README.md`, `Documents/dashboard-design.md`, `Documents/demo-script.md` and `Documents/build-plan.md` (step 8 done).

**Checks**
- `python -m pytest`: 287 passed, 1 skipped (symlink test on Windows). `npm run build` succeeds.
  - `tests/test_simulation_runs.py` covers:
    - reading step numbers and alert steps from a log;
    - who may see and report a run (other researchers never; the leader only in their projects);
    - bad alerts storing nothing, secrets blanked, the metric cap and thinning;
    - a watched run appearing at once and reaching the leader, and a rerun into the same log becoming a new run;
    - numbers waiting while the backend is down.
  - `tests/test_agent_chat.py` covers:
    - chat, scopes, instructions, history and access, including an A diagnosis, B diagnosis, then hub request with both findings in the hub's input;
    - the chat settings from `.env`, the chat agent getting only `question` and `context`, chat moving to it once it is set up, and the model reaching CREAO for chat but not for diagnoses.
- End to end on scratch data with the real backend, connector and dashboard, on separate ports:
  - Researcher A started the solver, watched it live from the Runs page, stopped it and saw it under Earlier runs;
  - the leader saw A's and B's runs and replayed one;
  - Researcher B only saw their own run.
- Screenshots of every page for both roles, light and dark, at 1440px and 1280px. No page errors and no sideways scrolling. The chat box, Send button and page content end on the same edge.
- Live CREAO runs (these used credits):
  - one hub run on synthetic findings (`32f10389-ee80-492c-8eac-0c60975c9b95`) cited both records and derived the refined-grid limit `dt <= 0.00625 s`;
  - six benchmark runs, all answer checks passed;
  - chat speed on the hub, timed from the question to the answer for the same three questions:

    | Question | Before | Fast model only | Fast model + direct answer (two runs) | + hub's chat skill in CREAO |
    |---|---|---|---|---|
    | "hello" | 21 s | 61 s | 19.9 s, 20.6 s | 11.2 s |
    | A and B results | 35.6 s | 60.7 s | 27.3 s, 31.2 s | 10.3 s |
    | "What else do you know" | 60.5 s | not run | 13.3 s, 10.0 s | 16.3 s |

    Replies now answer directly instead of narrating an audit. With the app changes alone, the hub kept a floor of about 20 s. That was its "Research Code & Reproducibility Audit" skill: `analysis_depth` was required with a `standard` default, so every message started an audit, and memory saved audit numbers after each reply. Replacing the hub's skill in CREAO with the chat skill in `Documents/super-brain-chat-agent.md` (Way A) brought replies to 10 to 16 s, about 3x faster than at the start.
- Real-project overview: run `416d9fb0` on a Fortran flagellum-hydrodynamics project gave an accurate overview.
- Secret scan: no API key or app ID in the commit.

---

## 2026-10-24 · `aac6cbf` · Rename to ExpandRG, straight design, demo launcher and project overview

**Why:** The project is now called ExpandRG (Expand Researching Globally), the dashboard gets a straight, clean look, a solo presenter gets a one-command demo, and a new member's agent can understand a whole project before diagnosing.

**Changed**
- Renamed the project to ExpandRG everywhere except past changelog entries and saved CREAO requests: text, docs, messages, settings (`EXPANDRG_HOME`, `EXPANDRG_DATA_DIR`, ...), the grants folder (`~/.expandrg`) and browser storage keys.
- Dashboard design: square edges everywhere (no rounded corners, pills or round counts), rectangular tags, a straight accent bar on the active page, thin dividers, a square "RG" tab icon, and the tagline on the sign-in page.
- Added `run_demo.py`: starts the backend, local connector and dashboard, grants the sample folders, and opens Researcher A and B side by side (`--fresh` resets the demo data, `--check` verifies and stops). It names a missing package and the Python it's running, instead of failing silently.
- Added `Documents/demo-script.md`: a minute-by-minute demo for one presenter playing every role.
- Project overview (build plan step 8): `local_connector/evidence/project_map.py` maps a granted folder on the researcher's computer (tree, plus the start of the README, dependency files, configs, main-script outlines and notebook headings; never sensitive, binary, tool or test files; under 24,000 characters). `POST /overview` re-checks the map and asks the researcher's own agent for an overview, saved privately (`GET /projects/{id}/overview`) and included in every later diagnosis on that project. Paths the agent invents are marked. Dashboard **Project** view and `python -m local_connector overview`.
- Real projects can join the workspace: `POST /projects` (leader) and `python -m backend.workspaces.demo --add-project ...`.
- Shared folder walking (`local_connector/file_access/walk.py`) now also skips `build/`, `dist/` and notebook checkpoints; the CREAO adapter reads any named JSON block (`extract_json_block`); one job runner serves diagnoses and overviews (`GET /jobs/{id}`).
- The dashboard's dev server refuses to move to another port, so the launcher can't be fooled by a leftover server.
- `.gitignore`: ignores `.demo-logs/`.
- Added `tests/test_project_overview.py`; updated `README.md` (show it, use it on your own project), `backend/README.md`, `frontend/README.md` and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 229 passed, 1 skipped (symlink test on Windows), with both the main Python and the `calhack` virtual environment. `npm run build` succeeds.
- `run_demo.py --check` with the `calhack` environment: everything starts, and nothing is left running afterwards.
- Screenshots of the restyled sign-in, Super Brain, finding, Alerts, Diagnose and Project views, in light and dark.
- The project map run on this repo picked the README, dependency files, configs and main scripts, and left out tests, tool folders and sensitive files.
- Secret scan: no API key or app ID in the commit.

---

## 2026-10-24 · `2a26834` · Add alert history, offboarding and the definition-of-done checks

**Why:** Build plan step 7 and the last items of the prototype definition of done, plus the dashboard click-through check from step 6.

**Changed**
- Alert history: a per-researcher setting, off by default (`GET`/`PUT /settings`). While on, alerts are also saved to the researcher's private memory (`GET`/`DELETE /alerts/history`), never to the shared database.
- Added `backend/knowledge_sharing/offboarding.py` and `POST`/`GET /offboarding` (leader only): revoke access, prepare eligible work from private memory (leaving out personal notes, rejected drafts, the agent's conversation text, already-shared work and other groups' content; secrets blanked), transfer and verify in one transaction, then retire the agent. A failed handoff stores nothing, is recorded as failed and can be retried. The membership policy is published at `GET /policy`.
- New tables, added to existing database files without a reset: `handoff_events` and `handoff_records` (shared), `settings` and `alerts` (private). Shared findings report `handed_off`.
- Demo: Researcher C with sample private work history for the offboarding demo (`python -m backend.workspaces.demo` adds it without resetting).
- Dashboard: an Offboarding view for the group leader (who gets Super Brain, Alerts and Offboarding), a "Save alert history" switch, a "handed off at offboarding" badge, and the policy on the sign-in page.
- CREAO adapter: a returned issue list now also counts as a real analysis, because run `461fed21` came back with no structured summary and no draft was made. That run is saved as a fixture.
- Findings saved in the same second are listed in the order they were saved.
- Added `tests/test_offboarding.py`, `tests/test_alert_history.py` and `tests/test_definition_of_done.py`.
- Updated `README.md`, `backend/README.md`, `Documents/dashboard-design.md` and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 203 passed, 1 skipped (symlink test on Windows). `npm run build` succeeds.
- Dashboard click-through with Playwright driving Edge, clicking only (runs `66a5d826` and `66bc53ce`): A diagnosed and shared; B got A's finding as context, shared, and B's finding is linked to A's; the leader offboarded C (3 handed off, 2 left out, every step done); C can no longer sign in. B's agent didn't name A's finding in its reply this time.
- Definition of done: no backend response contains a CREAO key or app ID; the dashboard never handles keys; a full connector session leaves every project file unchanged.
- The local demo database took the new tables and Researcher C without a reset; A's and B's findings were kept.
- Secret scan: no API key or app ID in the commit.

---

## 2026-10-24 · `1e557ad` · Add the dashboard, local anomaly checks and alerts

**Why:** Build plan step 6: researchers use SimGuard from a desktop dashboard, and get severity alerts from their running simulations without SimGuard ever touching the run.

**Changed**
- Added `local_connector/simulation_monitor/`: read-only checks on simulation logs (Critical for NaN/Inf, overflow-size values and error lines; Warning for sudden jumps and a steadily rising residual; Informational for configured thresholds and stalled output; Warning for a missing log), and a watcher that follows a growing log. Each check fires once per metric.
- Added `backend/monitoring/alerts.py` and endpoints `POST /alerts`, `GET /alerts`, `DELETE /alerts`: session-only, private to each researcher, never written to a database.
- Added `local_connector/service.py` (`python -m local_connector serve`, `127.0.0.1:8001`): granted folders, selectable files (no sensitive or binary files), evidence previews and log watching for the dashboard. Connector commands `serve` and `watch`. `GrantRegistry.check_path` checks a path that may not exist yet.
- Added the React dashboard in `frontend/` (Vite, plain CSS, light and dark): sign-in, Diagnose, Drafts, Super Brain and Alerts views, an alerts panel, and `?as=` / `&view=` to open a given member and view.
- Sample solver: `--delay` option for live demos (existing logs reproduce byte for byte); added a clean run log, `tests/fixtures/monitor/stable.log`.
- Added `tests/test_monitoring.py`.
- `.gitignore`: ignores `live.log` from demo runs.
- Updated `README.md` (running the dashboard, live alerts, line range `heat2d.py:18-25`), `frontend/README.md`, `backend/README.md`, `Documents/dashboard-design.md` and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 179 passed, 1 skipped (symlink test on Windows). `npm run build` succeeds.
- Real solver run watched through the connector and dashboard: the first Critical alert arrived and was shown while the solver was still running, and the solver ended on its own with its own exit code. Also an automated test.
- Headless-browser screenshots of every view at 1280px and 1440px, checked by eye; below 1280px the desktop notice appears.
- Not done: the full A → approve → B flow by clicking in the dashboard (needs a person; costs credits).
- Secret scan: no API key or app ID in the commit; `backend/.env`, `backend/data/` and `node_modules/` not included.

---

## 2026-10-23 · `1ffff36` · Add the prototype definition of done to the build plan

**Why:** The build plan's final check needs the full checklist in one place.

**Changed**
- Copied the prototype definition-of-done checklist into the build plan's final check, ticking what steps 1–5 have done.

**Checks**
- Secret scan: no API key or app ID in the commit.

---

## 2026-10-23 · `eae4dc0` · Add Super Brain search, links and diagnosis context

**Why:** Build plan step 5: Researcher B's agent can use Researcher A's approved finding, while every lookup, link and piece of context respects project access.

**Changed**
- `backend/memory/shared_findings.py`: `search_findings` (access decided in SQL before ranking by shared words), `get_readable_finding` and `related_findings` (links into projects the member can't read are left out).
- `backend/auth/permissions.py`: `context_project_ids`, so a diagnosis in project P only uses findings that everyone in P can already read.
- `/diagnose` looks up earlier findings from the symptom text only (on by default, `use_super_brain: false` turns it off) and sends up to 3 to the agent as `known_context`, labelled with their verification status. Jobs report `prior_findings`.
- Approving a draft links its finding (`associated_with`) to the findings its diagnosis used.
- New endpoints `GET /superbrain/search` and `GET /findings/{id}`; connector commands `ask` and `finding`, and `diagnose --no-super-brain`.
- Added `tests/fixtures/sample_project_b/` (Researcher B's refined grid, `dx = 0.005`, `dt = 0.01`, real blow-up log) and `tests/test_super_brain.py`.
- Updated `README.md` (B's part of the flow), `backend/README.md` and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 146 passed, 1 skipped (symlink test on Windows).
- Real run `93ceb050` on Researcher B's agent with A's finding as context: the reply relied on and checked A's finding `744083a8`, and explained that its `dt = 0.02` advice doesn't carry over because the limit scales with `dx²`. B's approved finding is linked to A's; B's search returns both.
- Secret scan: no API key or app ID in the commit; `backend/.env` and `backend/data/` not included.

---

## 2026-10-23 · `d6fec41` · Add approval path from private diagnosis to shared finding

**Why:** Build plan step 4: a diagnosis is saved privately with a draft finding, and only the researcher's explicit approval shares it to the Super Brain.

**Changed**
- `/diagnose` now requires sign-in and project membership, runs on the researcher's own personal agent (`AGENT_1` / `AGENT_2`), and only the researcher can read their jobs. Requests use `project_id`, `focus_areas` (list) and `analysis_depth`.
- Finished diagnoses are saved to the researcher's private memory as a hypothesis episode, with a draft built from the agent's top issue (symptom, likely cause, evidence, how to validate), always labelled `hypothesis`.
- Added `backend/knowledge_sharing/publishing.py`: approve (with optional edits; approval and finding written in one transaction, secrets blanked, never published twice) and reject (nothing written to the shared database).
- Added `backend/memory/shared_findings.py` and endpoints `GET /drafts`, `POST /drafts/{id}/approve`, `POST /drafts/{id}/reject`, `GET /projects/{id}/findings`.
- Database: private `share_drafts` table, `approvals.draft_id`, and `SCHEMA_VERSION` so old database files are refused with reset instructions.
- Local connector: `diagnose --as --project --focus --depth`, plus `drafts`, `approve`, `reject` and `findings` commands.
- Added `tests/test_knowledge_sharing.py`; rewrote `tests/test_diagnose_api.py` for the signed-in, per-researcher flow.
- Updated `README.md` (full A → approve → B flow), `backend/README.md` and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 131 passed, 1 skipped (symlink test on Windows).
- Real run `915f1428` on Researcher A's agent through the connector: draft created privately; Researcher B saw no findings before approval, saw the finding (still `hypothesis`, with provenance) after, and was refused on the wave project.
- The old local database was refused with reset instructions, then reset.
- Secret scan: no API key or app ID in the commit; `backend/.env` and `backend/data/` not included.

---

## 2026-10-23 · `524e7a1` · Add databases, demo workspace, sign-in and personal agents

**Why:** Build plan step 3 (SQLite models and demo data), plus the start of step 4: each researcher gets their own CREAO agent, so CREAO's own agent memory can't carry one researcher's evidence into another's runs.

**Changed**
- Added `backend/db/`: the shared Super Brain (`superbrain.db`) and one private memory file per researcher (`private/<member>.db`), with separate tables so no shared query can return private memory. `approvals` stores no draft text.
- Added `backend/workspaces/`: membership lookups (offboarded members have no project access) and the demo workspace: a group leader, Researcher A (both projects) and Researcher B (`heat-diffusion` only). Reset with `python -m backend.workspaces.demo --reset`.
- Added `backend/memory/private_memory.py`: private episodes split by project, with a membership check on every read and write.
- Added `backend/auth/`: prototype sign-in (`GET /demo/members`, `POST /sign-in`, `GET /me`) and project permission checks. Offboarding takes effect on the next request.
- CREAO adapter: `personal_agent()` for the researchers' agents (four required inputs, fixed choices checked before sending, `focus_areas` as comma-separated text, issue-list request). `analysis_ran` detection fixed: it now uses the `analysis_depth` the agent echoes back, because agents return zero counts and even a numeric score of 0 on runs that analyzed nothing.
- Added fixtures from real runs: `creao_agent2_run_not_analyzed.json`, `creao_personal_agent_contract_probe.json`, `creao_personal_agent_diagnosis.json`.
- Added `tests/test_database.py` and `tests/conftest.py` (every test uses temporary data folders), and new adapter tests.
- `.gitignore`: ignores `backend/data/`.
- Updated `backend/README.md` (databases, sign-in, agent input contracts, key behaviour) and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 105 passed, 1 skipped (symlink test on Windows).
- Demo database deleted, recreated and reloaded twice with the real command: same counts each time.
- Probe runs `8eb4dc7d` and `7bcbb28f`: both personal agents reachable, with identical input contracts.
- Real diagnosis `aaa9f564` on `AGENT_1`: comma-separated `focus_areas` accepted, 10 issues returned matching the summary counts, root cause found.
- Found: CREAO `capi_` keys can read other agents' runs, and listing runs returns 200 for unknown app IDs. Documented in `backend/README.md`.
- Secret scan: no API key or app ID in the commit; `backend/.env` and `backend/data/` not included.

---

## 2026-10-23 · `40e192e` · Add diagnosis endpoint, local connector and evidence rules

**Why:** Build plan step 2: a researcher can grant a project folder, pick the files that matter, and get a diagnosis, while secrets, sensitive files and local paths never leave the computer.

**Changed**
- Added `backend/orchestration/evidence_policy.py`: rules shared by the connector and the backend. Size limits (10 excerpts, 4,000 characters each, 12,000 total), relative paths only, sensitive files never sent (`.env`, private keys, credential files, `.git`, `.ssh`, …), and secrets inside files blanked out.
- Added `local_connector/file_access/`: folder grants stored in `~/.simguard/grants.json`. Reads are refused outside granted folders, for sensitive files and for binary files.
- Added `local_connector/evidence/`: keeps only the useful parts of the selected files (log head, lines around anomalies, log tail, or a chosen line range of code).
- Added `local_connector/__main__.py`: the command-line connector (`grant`, `list`, `revoke`, `diagnose`). `diagnose` shows exactly what will be sent and asks first.
- Added `backend/api/app.py` (FastAPI): `GET /health`, `POST /diagnose` (returns a job ID) and `GET /diagnose/{job_id}`. Added `backend/orchestration/diagnosis.py` and `jobs.py`: the backend re-checks all evidence before calling CREAO, and keeps jobs in memory for now.
- Added `tests/fixtures/sample_project/`: a small explicit heat solver with an unstable time step, and the real log it produces.
- Added `tests/test_file_access.py` and `tests/test_diagnose_api.py`.
- `backend/requirements.txt`: added fastapi, uvicorn and httpx.
- Updated `README.md` ("Try a diagnosis"), `backend/README.md` (endpoints and evidence rules) and `Documents/build-plan.md`.

**Checks**
- `python -m pytest`: 72 passed, 1 skipped (the symlink test needs extra permissions on Windows).
- Real run `e191c825` through the connector and backend: with only the symptom given, the agent found `r = 0.30 > 0.25` from the config and code.
- The connector refused to read `.env`.
- Secret scan: no API key or app ID in the commit; `backend/.env` not included.

---

## 2026-10-23 · `d679567` · Add CREAO adapter with issue list, tests and changelog

**Why:** Build plan step 1: one tested module for every CREAO call, and a way for the dashboard to get a list of issues instead of only prose.

**Changed**
- Added `backend/creao/` (`client.py`, `__init__.py`): the CREAO adapter. It checks required inputs before sending, starts runs (never retried, since each start costs credits), polls until done, keeps waiting through rate limits, gives clear errors (`CreaoInputError`, `CreaoRequestError`, `CreaoRunFailed`, `CreaoTimeout`) and never exposes the key.
- Hub runs now send an extra `output_format` input asking the agent to end its reply with a JSON issue list. The adapter moves it into `result.issues` and treats anything not labelled exactly `confirmed` as `suspected`.
- Added `tests/test_creao_client.py` (24 tests against a fake CREAO) and `pytest.ini`.
- Added fixtures `creao_hub_run_missing_inputs.json` (from run history, no credits) and `creao_hub_run_with_issue_list.json`.
- Added `backend/requirements.txt` (requests, python-dotenv, pytest).
- Added this `CHANGELOG.md`, updated with every commit.
- Updated `README.md`, `backend/README.md`, `Documents/build-plan.md` and `Documents/dashboard-design.md`.

**Checks**
- `python -m pytest`: 24 passed, no network or credits.
- Real run `790fe5fd` through the adapter: completed, 6 issues returned (2 confirmed, 4 suspected), matching the agent's summary counts.
- Adapter read back both earlier real runs correctly (status checks, no credits).
- Secret scan: no API key or app ID in the commit; `backend/.env` not included.
- Not done: credits per run still need to be read in the CREAO app.

---

## 2026-10-23 · `c27bea3` · Adopt SimGuard product decisions and verify CREAO hub agent

**Why:** The SimGuard product decisions were settled, so the repo and docs were brought in line with them, and step 1 of the build plan (verify CREAO) was started.

**Changed**
- Restructured folders to the SimGuard layout: `backend/` (`api`, `auth`, `workspaces`, `creao`, `orchestration`, `memory`, `monitoring`, `knowledge_sharing`, `db`), `local_connector/` (`file_access`, `evidence`, `simulation_monitor`) and `tests/`. Removed `agents/`, `shared/`, `infra/`, `scripts/`, `sample-data/` and the empty frontend page folders.
- Rewrote `Documents/build-plan.md` around the 7 steps of the first milestone, each with a checkpoint.
- Renamed `Documents/website-architecture.md` to `Documents/dashboard-design.md` and narrowed it to the dashboard UI.
- Added `backend/testcreo.py` (working CREAO smoke test) and `backend/.env.example`.
- Added `tests/fixtures/creao_hub_run_completed.json`: a real known-good hub agent run (app ID redacted), for use as the mock CREAO.
- `.gitignore`: now ignores SQLite databases and `.pytest_cache/`, and keeps `.env.example` tracked.
- Rewrote `README.md` and `backend/README.md` (adds the verified CREAO API contract).

**Checks**
- Hub agent run with all three inputs completed in about 60 s and correctly identified the time-step stability violation.
- Secret scan of the commit: no API key or app ID; `backend/.env` not included.

---

## 2026-10-23 · `986e2c3` · Set up project structure and website architecture skeleton

**Why:** First commit: an organized starting structure before any code.

**Changed**
- Added the initial folder layout (`frontend/`, `backend/`, `agents/`, `shared/`, `infra/`, `scripts/`, `sample-data/`), each with a README. Replaced in `c27bea3`.
- Added `Documents/website-architecture.md`: a desktop-first website architecture skeleton.
- Added `README.md` and a `.gitignore` for secrets and build output.

**Checks**
- None (documentation and empty folders only).
