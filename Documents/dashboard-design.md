# ExpandRG — Dashboard Design

**Status:** Skeleton v0.2 · UI only.
**Target devices:** Laptops and desktop computers (desktop-first; phones are not a design target)
**Date:** October 2026

---

## 1. Scope

- The React dashboard used by researchers and the group leader.
- **V1 includes:** workspace/project selection, diagnoses, severity alerts, approvals, Super Brain search, a few settings, and (optional) the offboarding demo.
- **Not in V1:** email or desktop notifications (alerts appear only in the dashboard), real-time streaming, onboarding curriculum UI, phone layouts.

---

## 2. Device and browser targets

| Item | Target |
|---|---|
| Primary devices | Laptop and desktop |
| Design width | 1440px (TBD) |
| Minimum supported width | 1280px (TBD) |
| Browsers | Latest Chrome, Edge, Firefox, Safari (TBD) |
| Input | Mouse or trackpad + keyboard |
| Phone | Not supported; show a "best viewed on a laptop or desktop" notice |

Desktop-first allows multi-pane layouts, side-by-side evidence and diagnosis, keyboard shortcuts, and hover tooltips.

---

## 3. Demo users

| User | Role in the demo | Can see |
|---|---|---|
| Researcher A | Hits the first simulation issue and approves sharing the finding | _TODO: projects_ |
| Researcher B | Hits a related issue and gets A's finding from the Super Brain | _TODO: projects (must exclude one of A's projects, for the isolation test)_ |
| Group leader | Starts the offboarding demo (optional) | _TODO_ |

---

## 4. Views

```
Dashboard
├── Workspace & project picker   (+ demo researcher switcher)
├── Diagnosis                    (agent conversation, evidence, proposed fix)
├── Runs                         (watched runs, live and earlier, with graphs and alerts)
├── Visualize                    (runs and experiments on one graph, changed by hand or by your own agent)
├── Experiments                  (changes tried in copies, run on the researcher's click, compared with the original)
├── Approvals                    (knowledge contributions, proposed code edits)
├── Super Brain                  (shared updates by researcher, "Ask Super Brain" box at the bottom)
├── Settings                     (automatic Super Brain search)
├── Projects                     (leader only: the group's projects, and a new one)
└── Offboarding                  (leader only, optional)
```

Built so far: researchers get Project, Diagnose, Drafts, Super Brain, Simulations (with Runs as its Monitor tab), Visualize and Experiments; the group leader (who has no personal agent) gets Super Brain, Run results, Visualize, Projects and Offboarding. Every page except Projects and Offboarding works on the project chosen in the top bar. "Give my agent relevant findings from the Super Brain" lives in Diagnose.

> Template for each view: **Purpose · Shows · Actions · Rules**

### 4.1 Workspace & project picker
- Purpose: _TODO_
- Shows: _TODO_
- Actions: _TODO_
- Rules: researchers only see projects they're members of.

### 4.2 Diagnosis
- Purpose: _TODO_
- Shows: observed symptoms, retrieved prior evidence, hypothesis, confidence, proposed validation steps, each labelled separately
- Data available now from the CREAO adapter: the prose reply, summary counts, and an issue list (title, agent status, confidence, evidence, proposed validation)
- Actions: _TODO_
- Rules: a proposed code edit is never applied without approval.

### 4.3 Runs (the nav item formerly called Alerts)
- Purpose: watch a simulation live, get its alerts, and open earlier runs again.
- Flow: the researcher runs the simulation on their computer, picks its log here and clicks **Start watching**; the local connector reads the log and sends the numbers from each step and the alerts; the run shows here live.
- Shows: one sentence on what is watched (the log file the simulation writes; ExpandRG only reads it), then, for researchers, a "How to use it" box (start the simulation writing a log file, pick it and click Start watching, read the graphs and problems), open until the first run exists, and for the leader the same Warning / Critical guide on its own. (Researcher only) a "Log file to watch" bar: the log (from granted folders, refreshed every few seconds), **Start watching**, and "Add a folder". Then two columns: on the left the runs in this project, **Being watched now** first, then **Finished** (log name, who for the leader, start time, problem counts or "No problems found"); on the right the chosen run: name, Watching now or Finished, whose simulation, when watching started, the step reached, the last new line; the two latest problems found (or "No problems found so far"); up to 3 line charts, one per measure read from the log (e.g. max|T| and residual) on a shared step axis, with a log scale when values span 1,000x or more and Warning and Critical alerts marked at their step (hover shows the value at that step in every chart); then the run's alerts, newest first, and the numbers as a table.
- Actions: **Stop watching** (a live run watched on this computer); **Replay** (an earlier run: the graphs and alerts play back step by step in about 8 seconds); **Open in Visualize** (the run on its own graph there) and, for researchers, **Edit with Agent** (the same, with the cursor in the agent box).
- The leader's page is **Run results**, not a live monitor.
  - What it is: what researchers shared. Each run is a log and its data from a researcher's computer, drawn as graphs to see what happened, with its problems marked.
  - One list, "Shared runs, newest first": run name, researcher, date, steps recorded, and problem counts. The newest run opens first.
  - The run's line reads "Shared by Researcher A · date · N steps recorded", plus "more of its log is still arriving" only while data keeps coming. There are no Live/Finished tags.
  - **Play back step by step** replays it.
  - No red live-problem count in the leader's menu.
- In progress means new data arrived in the last 5 minutes. A run whose watch ended without saying so (ExpandRG restarted, say) counts as finished after that, for researchers too, and in the menu badge.
- Rules: ExpandRG only reads the log and never pauses or stops a simulation. The log file never leaves the researcher's computer, only the numbers and alerts. A run is seen by the researcher and the group leader, never by other researchers. The Runs tab shows a count of Critical alerts in runs being watched right now.

### 4.3b Experiments (researchers)
- Purpose: try a change in a copy of the project and compare it with the original run, without touching the project.
- Shows: the project folder, then the run manifest's entry point, "Compare with" (a log in the project, e.g. `run.log`), and up to three experiments as rows of parameter values (values that differ from the config are highlighted). Below, one card per comparison: each experiment with its status (prepared, queued, running, finished, failed, stopped) and time limit; one line chart per measure with the original in gray and the experiments in blue, orange and green, a shared legend and a hover readout of every run at that step; and a table with each run's result, last step, final values and first Warning and Critical steps.
- Actions: **Prepare** (copies only), **Run**, **Run all** (one at a time, up to the sum of their time limits), **Stop**, **Delete**; on a comparison with numbers, **Open in Visualize** and **Edit with Agent** (the original and the experiments on one graph).
- Rules: nothing runs without a click. Runs happen in copies under `~/.expandrg/experiments/`, limited in time, memory and output, with keys and tokens removed from their environment. Only commands in the project's `expandrg.json` run, and only its listed config keys can change. Experiments stay on the researcher's computer.

### 4.3c Visualize
- Purpose: compare runs and experiments on one graph, and change it by hand or by asking your own agent ([design](visualization-design.md)).
- Shows, top to bottom:
  - the title, a line on where the numbers come from, and the saved-graph controls (a list of saved graphs, **New**, **Save** or "Saved", **Undo**, **Delete**);
  - **Runs** (left): a checkbox per run or experiment with its colour swatch once on the graph, whether it's a watched run, an experiment or an original run, the date, whose it is when it isn't yours, its Critical count, and an experiment's changed parameters;
  - the controls (right): measure chips (up to 3), X axis (step or a logged measure), Graph (Line, Scatter), Runs (Together, Separate), Y scale (Linear, Log), Style (Screen, Paper, Poster), Range of steps (from, to, **Apply**, **Show all**), Title, and under "Units and notes" a unit per measure and notes at an x value;
  - the graph card, full width: the graph (Plotly: zoom, pan, hover, legend toggles), **PNG** and **SVG**, then what wasn't applied and why, the notes (what the graph can't show, a range that hides an alert, whose alert lines they are), the comparisons (largest difference where runs overlap, and where), and **Numbers**: each line's points, lowest, highest, last value and first NaN/Inf, from every point;
  - **Edit with Agent** (researchers, right; below the graph under 1180 px): your agent's replies, each with chips for what it changed and what wasn't applied, and a message box. "Working on it… Ns" while it works.
- Rules:
  - The graph is drawn by ExpandRG from the runs' own numbers. Every change, yours or the agent's, is an operation checked first; nothing changes the runs.
  - One panel per measure, never a second y axis.
  - A run keeps its colour when others are added or removed: an 8-colour palette checked for colour blindness, with its own steps for dark mode.
  - A legend for two or more runs, labels at line ends for up to four, and the Numbers table, so colour and hover are never the only way to read it. Grid lines are solid and faint.
  - Saved graphs are private. After **Save**, every change is saved as it's made. Exports are light on white whatever the page's theme.
  - The agent sees summaries and statistics, not the data, and only you set units. Each message uses credits, and the graph is saved first so the agent's changes can be undone.

### 4.4 Approvals
- Purpose: _TODO_
- Shows: the agent's editable draft (research result, code bug / new implementation, or error diagnosis)
- Actions: approve, edit, reject
- Rules: no diagnosis reaches the Super Brain without approval. (What a personal agent establishes in chat it shares itself, as a hypothesis: see 4.5.)

### 4.5 Super Brain
- Purpose: one place to see what the group has shared, and to ask it for more detail.
- Shows, top to bottom, for the project chosen in the sidebar's project switcher: four counts (updates shared, researchers sharing, verified, still hypotheses), a bar chart of updates shared per researcher (hover or focus a bar for the status breakdown, latest date and projects; click it to jump to that researcher), then each researcher's updates, newest first. Each update is plain: muted labels in one column (Issue, Likely cause, Run) and the values beside them, with no coloured boxes. It shows:
  - a plain name: the title without its numbers and formulas (the full title is in its tooltip and on its page);
  - its tags (verification, shared by their agent, handed off), then its category, project and date;
  - **Issue**: the summary's "Symptom" part, or its first sentence;
  - **Likely cause**: the summary's "Cause" or "Likely cause" part, or else the agent's guess;
  - the graph of the run it came from (its first measure, with Warning and Critical marks), when a run is attached and the viewer may see that run (the researcher and the leader); other researchers see no graph;
  - **Comments (n)**: a thread the leader and the project's researchers all write in and see. Agents don't read it.
- Updates a personal agent shared from chat carry a "shared by their agent" tag. Their page shows "shared by <name>'s agent from a chat" and, apart from the evidence, "The agent's own guess · not evidence".
- Every update's page has "Added by the group": the notes members added, each labelled More detail, My own guess or Correction, with who and when. Below it, a form to add one (choose the kind, write up to 2,000 characters, **Add to this finding**). When the agent missed something, the empty list says so and points to the form.
- Drafts: when sharing, **Attach a run** lists the researcher's runs in that project. A run started up to a day before the draft is already selected.
- Actions: open an update for the full finding, its links, notes and comments; add a note or a comment; ask a question in the "Ask Super Brain" panel to the right of the updates (below them on narrower screens). Your messages are on the right, its answers on the left, newest at the bottom, from the chosen project. Each shows the question and the answer, labelled "Super Brain · CREAO": no timing line and no list of the findings used (while it works: "Super Brain is thinking… Ns"). The panel's header holds the Knowledge scope, Instructions and Clear chat.
- Tabs at the top, also under Super Brain in the sidebar: **Updates** (all of the above) and **Worksheets**, the Super Brain as a teacher. Worksheets use the full width, without the talk panel. `?tab=worksheets&worksheet=<id>` opens one directly.
  - Leader, top right: **New worksheet** opens a panel.
    - **For:** a checkbox per researcher with how many findings from the others they'd learn from (or "nothing new since their last worksheet").
    - Two tabs. **Ask the Super Brain**: "What should it cover?" (optional; empty teaches what the others found) and an "Only findings that are new to each person" checkbox, on by default. **Write it myself**: an optional title, numbered items (Question or Try it, the text, an optional model answer, remove ×), **Add a question** (up to 8), and "Anything CREAO should know?".
    - Asking with nothing in "What should it cover?" skips anyone with nothing new, and says who.
    - **Send to CREAO (k)** starts one run that writes one worksheet for each person picked, showing who it's writing for and the seconds passing; then "Ready: Worksheet 2 for Researcher B, …".
  - Leader, below: **All worksheets**, newest first: researcher, "Worksheet N · title", the date made, status (Waiting for answers, Ready to check, Checked, or Cancelled), how many items are answered, and **Cancel** or **Restore**. A cancelled row is greyed; its number is taken by that person's next worksheet. A worksheet shows each item with the researcher's answer, the model answer, and a comment box; **Send comments** (or **Update comments**) and **Delete** are at the end, with a comment on the whole worksheet.
  - Researcher: their own worksheets (To do, Sent to the leader, Checked by the leader). Items are grouped by topic and numbered: **Question** or **Try it**, with the findings they come from (links). Each has an answer box, and a "try it" step says to use a copy on the Experiments page. **Save** and **Send to the leader**; answers can change until the leader has checked them. Model answers and the leader's comments appear once it's checked.
- Rules: results respect project access; hypotheses never look like verified facts. The chart uses one colour; every number in it is also written in the researcher sections, so the tooltip is never the only way to read it.

### 4.6 Settings
- Automatic Super Brain search: **on** by default
- _TODO_

### 4.7 Projects (leader only)
- Purpose: start using ExpandRG on another project without the terminal.
- Shows: "The group's projects" (name and ID), then **New project**: a name, a checkbox per active researcher (all ticked), "You're on every project", and **Create project**.
- After creating: the new project is chosen in the project switcher, and a note says researchers see it after reloading, and in VS Code by opening its folder and clicking **ExpandRG: Set up**.
- Rules: the ID comes from the name (lowercase, dashes); a name already used is refused with the reason. In a group made with `setup_expandrg.py`, the project is also written to `group.json`, which VS Code's Set up reads.

### 4.8 Offboarding (optional, step 7)
- Purpose: _TODO_
- Shows: handoff progress: revoke → prepare → transfer → verify → deactivate
- Rules: report a failed handoff honestly; never show "complete" unless verified.

---

## 5. Layout

### 5.1 App shell

```
┌──────────────┬───────────────────────────────────────────────────┐
│ ExpandRG     │ Project / Page                  Member ▾   ☾/☀    │  top bar (sticky)
│ [Project ▾]  ├───────────────────────────────────────────────────┤
│              │                                                   │
│ RESEARCH     │                                                   │
│  Project     │                 Page                              │
│  Diagnose    │                                                   │
│  Drafts      │   (Super Brain: updates │ Ask Super Brain panel)  │
│ KNOWLEDGE    │                                                   │
│  Super Brain │                                                   │
│   Updates    │                                                   │
│   Worksheets │                                                   │
│ SIMULATION   │                                                   │
│  …           │                                                   │
│──────────────│                                                   │
│ (RA) Member ▾│                                                   │
└──────────────┴───────────────────────────────────────────────────┘
```

- **Sidebar** (fixed): the ExpandRG mark, the **project switcher** (the leader also gets **New project…**, which opens Projects), the navigation in groups, and the signed-in member at the bottom.
  - Researcher groups: Research (Project, Diagnose, Drafts), Knowledge (Super Brain), Simulation (Simulations, Visualize, Experiments).
  - Leader groups: Knowledge (Super Brain), Group (Run results, Visualize, Projects, Offboarding).
  - Super Brain and Simulations show their tabs under them when open: Updates and Worksheets; Set up & run and Monitor.
  - Counts: pending drafts on Drafts; Critical problems in a researcher's live run on Simulations (red).
- **Top bar** (sticky): project / page breadcrumbs, the member chip and the light/dark toggle.
- **Member menu** (the chip or the sidebar's member card): switch to another member in one click (prototype sign-in), or sign out.
- **Page:** up to 1180 px wide, centred; the Super Brain and Visualize use up to 1440 px, for the talk panel or the agent panel beside the page.

Two edges can be dragged to resize:
- the sidebar's right edge sets its width (200 px up to 420 px or half the window);
- the talk panel's left edge sets its width (300 px up to 680 px or half the window).

The edges light up on hover. They are keyboard-focusable separators: the arrow keys move them 16 px. A double-click resets to the default size. Sizes are kept in this browser (`localStorage`), and the page works without it.

### 5.2 Breakpoints

| Name | Width | Layout behaviour |
|---|---|---|
| Desktop / laptop | ≥ 1180px | Super Brain updates and talk panel side by side; Visualize's agent panel beside the graph |
| Small laptop | 860–1179px | The talk panel goes under the updates, and Visualize's agent panel under the graph; stat tiles two by two below 1100px |
| Below minimum | < 860px | The sidebar becomes a drawer behind the menu button; a notice suggests a wider window. Visualize's runs list goes above its controls |

### 5.3 Visual language

| Thing | Needs a distinct look | Notes |
|---|---|---|
| Alert severity | Informational, Warning, Critical | Blue, amber, red (soft background, coloured text) |
| Verification status | Hypothesis, supported, verified, unresolved | A hypothesis must never look like a fact |
| Agent's issue status | Agent-confirmed vs suspected | Agent-confirmed is still not researcher-verified; style it differently from verified |
| Private vs shared | Private project memory vs Super Brain finding | _TODO_ |

---

## 6. Frontend decisions

| Decision | Choice | Notes |
|---|---|---|
| Framework | React 18 | Decided |
| Getting updates | Polling | Alerts and drafts every 3 s, watches every 2 s, diagnosis jobs every 3 s. No real-time streaming in V1 |
| Build tool | Vite | Its dev server also passes `/api` to the backend and `/connector` to the local connector |
| Routing | None | Views switch inside the app; `?as=`, `&view=` and `&project=` open a given member and view |
| Graphs | Hand-drawn SVG for the run and experiment charts; Plotly (`plotly.js-dist-min` 2.35.2) only on Visualize | Plotly is 1.4 MB compressed, so it's its own file, loaded when Visualize opens. The figure comes from the backend; the page only applies the theme's colours |
| Styling / components | Plain CSS, no component library; Lucide icons (`lucide-react`) | A calm console look: soft surfaces, 1px borders, rounded cards, one indigo accent. Inter for text and JetBrains Mono for numbers and paths, from Google Fonts (system fonts when offline). Light and dark follow the system until the viewer picks one with the top bar toggle, which this browser remembers |

---

## 7. Open questions

| Question | Owner | Status |
|---|---|---|
| Minimum supported screen width? | _TODO_ | Open |
| Which projects does each demo researcher belong to? | _TODO_ | Open |
| _TODO_ | | |
