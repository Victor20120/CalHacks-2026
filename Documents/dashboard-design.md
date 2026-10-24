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
├── Experiments                  (changes tried in copies, run on the researcher's click, compared with the original)
├── Approvals                    (knowledge contributions, proposed code edits)
├── Super Brain                  (shared updates by researcher, "Ask Super Brain" box at the bottom)
├── Settings                     (automatic Super Brain search)
└── Offboarding                  (leader only, optional)
```

Built so far: researchers get Project, Diagnose, Drafts, Super Brain and Runs; the group leader (who has no personal agent) gets Super Brain, Runs and Offboarding. Every page works on the project chosen in the top bar. "Give my agent relevant findings from the Super Brain" lives in Diagnose.

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
- Actions: **Stop watching** (a live run watched on this computer); **Replay** (an earlier run: the graphs and alerts play back step by step in about 8 seconds).
- Rules: ExpandRG only reads the log and never pauses or stops a simulation. The log file never leaves the researcher's computer, only the numbers and alerts. A run is seen by the researcher and the group leader, never by other researchers. The Runs tab shows a count of Critical alerts in runs being watched right now.

### 4.3b Experiments (researchers)
- Purpose: try a change in a copy of the project and compare it with the original run, without touching the project.
- Shows: the project folder, then the run manifest's entry point, "Compare with" (a log in the project, e.g. `run.log`), and up to three experiments as rows of parameter values (values that differ from the config are highlighted). Below, one card per comparison: each experiment with its status (prepared, queued, running, finished, failed, stopped) and time limit; one line chart per measure with the original in gray and the experiments in blue, orange and green, a shared legend and a hover readout of every run at that step; and a table with each run's result, last step, final values and first Warning and Critical steps.
- Actions: **Prepare** (copies only), **Run**, **Run all** (one at a time, up to the sum of their time limits), **Stop**, **Delete**.
- Rules: nothing runs without a click. Runs happen in copies under `~/.expandrg/experiments/`, limited in time, memory and output, with keys and tokens removed from their environment. Only commands in the project's `expandrg.json` run, and only its listed config keys can change. Experiments stay on the researcher's computer.

### 4.4 Approvals
- Purpose: _TODO_
- Shows: the agent's editable draft (research result, code bug / new implementation, or error diagnosis)
- Actions: approve, edit, reject
- Rules: no diagnosis reaches the Super Brain without approval. (What a personal agent establishes in chat it shares itself, as a hypothesis: see 4.5.)

### 4.5 Super Brain
- Purpose: one place to see what the group has shared, and to ask it for more detail.
- Shows, top to bottom, for the project chosen in the top bar: four counts (updates shared, researchers sharing, verified, still hypotheses), a bar chart of updates shared per researcher (hover or focus a bar for the status breakdown, latest date and projects; click it to jump to that researcher), then each researcher's updates, newest first: title, verification tag, the issue (the summary's "Symptom" line), category, project and date.
- Updates a personal agent shared from chat carry a "shared by their agent" tag. Their page shows "shared by <name>'s agent from a chat" and, apart from the evidence, "The agent's own guess · not evidence".
- Every update's page has "Added by the group": the notes members added, each labelled More detail, My own guess or Correction, with who and when. Below it, a form to add one (choose the kind, write up to 2,000 characters, **Add to this finding**). When the agent missed something, the empty list says so and points to the form.
- Actions: open an update for the full finding, its links and notes; add a note; ask a question in the "Ask Super Brain" box docked at the bottom of the page. Answers stack just above the box, newest at the bottom, from the chosen project.
- Rules: results respect project access; hypotheses never look like verified facts. The chart uses one colour and square bar ends; every number in it is also written in the researcher sections, so the tooltip is never the only way to read it.

### 4.6 Settings
- Automatic Super Brain search: **on** by default
- _TODO_

### 4.7 Offboarding (optional, step 7)
- Purpose: _TODO_
- Shows: handoff progress: revoke → prepare → transfer → verify → deactivate
- Rules: report a failed handoff honestly; never show "complete" unless verified.

---

## 5. Layout

### 5.1 App shell

```
┌──────────────────────────────────────────────────────────────────┐
│  Top bar: ExpandRG · project picker · member                     │
├────────────┬─────────────────────────────────────────────────────┤
│            │                                                     │
│  Left nav  │                   Main view                         │
│            │                                                     │
└────────────┴─────────────────────────────────────────────────────┘
```

The shell fills the window: the top bar and left nav stay in place and only the main view scrolls. Alerts live with their run on the Runs page. On the Super Brain, the "Ask Super Brain" box is docked at the bottom of the main view, with answers just above it.

### 5.2 Breakpoints

| Name | Width | Layout behaviour |
|---|---|---|
| Large desktop | ≥ 1600px | _TODO_ |
| Desktop / laptop | 1280–1599px | _TODO_ |
| Below minimum | < 1280px | _TODO_ |

### 5.3 Visual language

| Thing | Needs a distinct look | Notes |
|---|---|---|
| Alert severity | Informational, Warning, Critical | _TODO: colours_ |
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
| Styling / components | Plain CSS, no component library | Light and dark themes follow the system setting |

---

## 7. Open questions

| Question | Owner | Status |
|---|---|---|
| Minimum supported screen width? | _TODO_ | Open |
| Which projects does each demo researcher belong to? | _TODO_ | Open |
| _TODO_ | | |
