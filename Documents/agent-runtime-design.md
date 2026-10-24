# ExpandRG — Local Agent Runtime and Experiments (design)

**Status:** design agreed on 2026-10-24. Phase 1 of the build order (experiments without AI) is built, and the VS Code extension (phase 6) has been moved up and started: the connector accepts runs streamed from a terminal; the extension has a status bar, commands and a **▶ Run a simulation** button, watches runs typed in the VS Code terminal (by decision below), and starts ExpandRG in the background. See `vscode-extension/README.md`. The rest is not built, unless marked **exists**.

**VS Code decision (2026-10-24):** runs started in the VS Code terminal are watched automatically when the command is one of the project's `expandrg.json` entry points; other commands that look like runs (`python …`, `./…`, `mpirun …`) get a one-click "Watch this run?" pop-up; everything else is ignored.

This extends ExpandRG; it does not replace it. The goal is personal agents that investigate problems on their own, propose experiments and code changes, and learn from each other through the Super Brain, while every run and every permanent change stays under the researcher's control.

---

## Personal-agent conversation (implemented)

The extension's **My agent** panel connects directly through the backend to the configured researcher's personal CREAO agent. It supports private conversation, saved response instructions, **Understand project** with a local project-map preview and explicit send, and a saved overview used in subsequent questions. **Discuss simulation setup** helps the researcher ask about build systems, compilers, parameters and logs. Messages do not execute commands or apply file changes.

This builds the conversation and project-understanding part of the workflow. Structured generation/validation of executable build-and-run plans and applying those plans to the launcher are still future work. The existing Run button continues to launch configured manifest commands.

## 1. Principles

1. **Agents never start a run on their own.** Every run or experiment waits for the researcher's **Run** click. This is the one thing agents may not do; everything else in the investigation is autonomous.
2. **CREAO only thinks; the researcher's computer does everything else.** CREAO can't read local files or reach the researcher's computer: its API is request and reply. A local runtime decides what to send, reads CREAO's reply as a plan written as data, checks it against the researcher's rules, and prepares it locally.
3. **No cloud or HPC execution.** All code runs on the researcher's computer, in a throw-away copy of the project.
4. **The real project is never changed silently.** Permanent changes, merges, deletions and files outside the granted folders always need approval.
5. **Rules are enforced locally, not by CREAO.** CREAO's plans are untrusted input: code, logs and shared findings can contain text that tries to steer an agent.
6. **Diagnoses and investigation findings reach the Super Brain only with approval.** What a personal agent establishes in chat, it shares itself: always a hypothesis, marked "shared by their agent", with its own guess kept apart. Anyone in the project can add detail, their own guess or a correction (changed 2026-10-24, the group's choice).

---

## 2. Architecture

```
 RESEARCHER'S COMPUTER  (everything that reads files or runs code stays here)
┌───────────────────────────────────────────────────────────────────────────┐
│  VS Code extension ─┐  file + log watchers ─┐                             │
│                     ▼                       ▼                             │
│  ┌──── Local agent runtime (grows out of today's local connector) ─────┐  │
│  │ Observer → Signal filter (cheap local checks) → Orchestrator        │  │
│  │                                                    │        ▲       │  │
│  │ Context builder (privacy rules, redaction, send log)    plans as    │  │
│  │                                                    ▼    data        │  │
│  │ Policy engine (grants, approvals, budgets) ─► Execution engine      │  │
│  │                               (experiment copies, limits, kill)     │  │
│  └─────────────────────────────┬───────────────────────────────────────┘  │
└────────────────────────────────┼──────────────────────────────────────────┘
                                 │ context, findings, run numbers, approvals, tasks
                      ┌──────────▼───────────┐   reasoning only    ┌──────────────────┐
                      │ ExpandRG backend      │ ◄────────────────► │ CREAO            │
                      │ access control,       │                    │ personal agents, │
                      │ Super Brain store,    │                    │ Super Brain hub  │
                      │ CREAO keys + credits  │                    └──────────────────┘
                      └──────────┬───────────┘
                                 │
           Web dashboard: Researcher workspace · Research leader dashboard
```

| Component | Responsibility | Status |
|---|---|---|
| Local connector | Folder grants, sensitive-file rules, secret blanking, size limits, project map | **Exists** |
| Log watcher and cheap checks | NaN/Inf, overflow, sudden jumps, rising residual, stalls; live runs on the Runs page | **Exists** |
| Diagnosis and project overview | Personal CREAO agent on selected evidence, with a preview before sending | **Exists** |
| Super Brain | Drafts → approval → shared findings, access control, hub chat | **Exists** |
| VS Code extension | Reports terminal runs (command, log file, exit code); **Run a simulation**; shows notifications | **Started** (decision 6); saves not yet |
| Observer and signal filter | Turns activity into signals: alerts, failed runs, rule breaks, big solver changes | Partly exists |
| Orchestrator | Runs an investigation: suspect → hypothesize → prepare experiments → (researcher runs) → compare → report | Proposed |
| Context builder | Decides what may go to CREAO under the project's privacy rules; logs every send | Extends existing evidence rules |
| Policy engine | Checks every plan against grants, run manifests, budgets and approval rules | Proposed |
| Execution engine | Experiment copies, the process sandbox, limits, kill switch, result comparison | Proposed |
| Backend | Access control, Super Brain store, run history, approval queue, CREAO keys and credits | **Exists**, extended |
| CREAO personal agents | Investigate, propose hypotheses and experiments, interpret results | **Exist** (audit skill) |
| CREAO hub | Super Brain chat: answers from the group's findings | **Exists** (chat skill) |

CREAO calls keep going through the backend, as today, so the keys and credit tracking stay in one place.

---

## 3. What agents do on their own, and what needs the researcher

| Agents do on their own | Agents need the researcher first |
|---|---|
| Watch code changes, logs and the researcher's runs | **Start any run or experiment** |
| Run cheap local checks and open investigations | Change the real project code, or merge |
| Look up the Super Brain | Read files outside the granted folders |
| Ask CREAO, within the project's privacy rules | Delete or overwrite anything |
| **Prepare** experiments: make the copy, apply the change, write the plan | Share a finding to the Super Brain |
| Compare results of approved runs, write reports and draft findings | Send information to CREAO outside the privacy rules |

---

## 4. Decisions

| # | Decision | Chosen | Why |
|---|---|---|---|
| 1 | How strong the experiment sandbox is | Process sandbox now (copies, time and memory limits, kill switch); containers optional later | Every run is approved by the researcher, so nothing runs unseen; limits still stop runaway runs |
| 2 | How runs are approved | Each experiment has **Run**; an investigation's set has **Run all** within a stated total budget | Control without approving five experiments one by one; anything beyond the approved budget asks again |
| 3 | What goes to CREAO without asking | Per-project tick boxes. Defaults: alert lines, run numbers, error messages and shared findings go automatically; configs and code excerpts ask first until ticked. Pause switch per project | The researcher sets the privacy trade-off per project |
| 4 | New local checks and labels | Physical rules per project and baseline comparison now; convergence only as proposed experiments; statistical detection later. Labels below | Cheap checks first, so CREAO is only asked when something is worth it |
| 5 | Super Brain sharing and the leader | Findings shared only after approval, except what an agent establishes in chat (shared by the agent as a hypothesis, changed 2026-10-24); members and the leader add detail, guesses and corrections; the leader requests investigations that the researcher accepts | Keeps the group informed without a draft for every chat, while an agent's word is never taken as verified |
| 6 | VS Code integration | A light extension first; a chat panel and inline notes later | Watching starts by itself, while chat and approvals stay in one place |

---

## 5. Example: one investigation

1. The researcher runs the heat solver from VS Code. The extension reports the run; the log watcher sees max|T| jump at step 200 and raises a Warning.
2. The orchestrator opens an investigation and looks up the Super Brain (no CREAO run). It finds Researcher A's CFL finding.
3. The context builder packs what the project's privacy rules allow: the alert lines, the run numbers, the finding, and (if ticked) the `config.json` excerpt. Secrets are blanked and the send is logged.
4. **CREAO run 1.** The personal agent replies with a JSON block next to its prose:
   - hypotheses, e.g. "dt above the stability limit";
   - proposed experiments, e.g. "set dt = 0.02 in a copy, run 500 steps, about 2 CPU-minutes; expected: max|T| stays bounded".

   This builds on today's named JSON blocks (`extract_json_block`).
5. The policy engine checks each experiment: the entry point is in the project's run manifest, the change only touches allowed parameters, and the budget is within limits. Experiments that fail a check are shown as "needs a closer look" instead.
6. The execution engine prepares each experiment copy and **stops**. The researcher's workspace shows the proposals with **Run** and **Run all**.
7. The researcher clicks **Run**. The experiment runs under limits, and the existing checks watch it like any run.
8. **Compare.** The original blew up at step 2223; the experiment stays stable. The evidence supports the hypothesis.
9. **CREAO run 2 (optional).** The agent interprets the results and drafts a finding.
10. The workspace shows *anomaly → hypothesis → experiment → result*, a **proposed change awaiting approval** (`dt = 0.02` in `config.json`), and a draft finding. With the researcher's approval the finding goes to the Super Brain, where the leader can comment.

That's at most 2 CREAO runs for the whole investigation.

---

## 6. Experiment environment

- **Experiment copy.**
  - For a git project, a `git worktree` at the current commit, with uncommitted changes applied as a patch.
  - Otherwise, a copy of the granted folder.

  Large data stays read-only and is never copied. Copies live under `~/.expandrg/experiments/<id>/` and are deleted after a set number of days.
- **Run manifest (per project).** The entry points experiments may use and the parameters they may change, e.g. `python heat2d.py {config}` with `dt` and `steps` overridable. Changes to solver source code, or commands outside the manifest, are shown as such so the researcher can read them before clicking Run.
- **Process sandbox (decision 1).**
  - The run's folder is the experiment copy.
  - Secrets are removed from the environment.
  - Limits on wall time, CPU time, memory and output size, using Job Objects on Windows and rlimits on Linux and macOS.
  - The run is stopped when a limit is reached.
  - A plain process can't be fully cut off from the network or other folders, which is acceptable because the researcher approves every run.
- **Swappable sandbox.** Containers (Docker or Podman: no network, the project mounted read-only, resource limits) can be added later behind the same executor interface, as can VMs or WSL2.
- **Results.** The run's output, the numbers read by the existing log parser, a comparison with the original run or baseline, and size-capped artifacts. They're stored in the researcher's private history.
- **Making a change permanent.** The change, its evidence and the experiment ID go into the approval queue. Once approved, it's applied as a git branch or commit in the real project, never silently.
- **Always:** a kill switch for every running experiment, and an audit log of every action.

---

## 7. Monitoring and labels (decision 4)

Checks run locally, cheapest first:

| Check | When | Status |
|---|---|---|
| NaN/Inf, overflow, sudden jumps, rising residual, stalls, thresholds | Every watched run | **Exists** |
| Physical rules per project, e.g. `T >= 0`, `max|T| <= initial`, `energy drift < 1%` | Every watched run | Proposed |
| Comparison with a baseline run the researcher marked as good | Every watched run | Proposed |
| Convergence, e.g. dx vs dx/2 | Only as a proposed experiment (needs Run) | Proposed |
| Statistical detection on run history | Later, once there is history | Later |

Labels on a signal or finding:
- **Anomaly:** a check fired.
- **Suspected bug:** the agent has a hypothesis.
- **Confirmed bug:** an approved experiment reproduced it *and* the researcher confirms it.
- **Scientific behaviour** or **false alarm:** set by the researcher.

The researcher's label is always final, so real scientific behaviour isn't "fixed" away.

---

## 8. Privacy: what CREAO receives (decision 3)

- **Never sent, whatever the settings:** `.env` files, keys, `.git`, whole repositories, data files and absolute paths on the computer. Secrets are blanked and size limits apply. **Exists.**
- **Sent automatically per project's tick boxes:**

  | Kind of information | Default for a new project |
  |---|---|
  | Alert lines and run numbers | Automatic |
  | Error messages | Automatic |
  | Shared findings | Automatic |
  | Config files | Ask first |
  | Code excerpts | Ask first |

- **Anything not ticked** asks first, with today's preview.
- **Pause switch** per project: nothing goes to CREAO automatically while it's on.
- **Send log:** every send is recorded locally and viewable in the workspace.
- CREAO may keep what it receives. Isolating CREAO's own memory is outside ExpandRG's control.

---

## 9. Keeping CREAO usage down

- **Only on events.** CREAO is asked only for a Warning or worse, a failed run, a rule break, the researcher's question, or a leader's investigation request that the researcher accepted. Routine edits never trigger it.
- **Local checks first** (section 7) decide whether anything is worth a run.
- **Run cap per investigation:** 2 by default, one for hypotheses and one to interpret results.
- **Small, reused context.** Today's size limits, code-change summaries instead of whole files, and the saved project overview.
- **Matched speed.**
  - Triage depth first, deep only when asked.
  - Chat uses the fast model and the hub's chat skill: 10 to 16 s, measured on 2026-10-24.
  - A triage diagnosis took 89 s.
- **No repeats.** The same alert or question with the same context reuses the earlier answer.

---

## 10. Super Brain and the leader (decision 5)

- **Upward: findings only, never logs.** Investigations produce draft findings with a title, summary, status, evidence and experiment ID. They're shared only after the researcher approves. Run numbers stay in the Runs store.
- **From chat: the agent shares.** What a personal agent establishes in chat goes to the Super Brain at once, as a hypothesis marked "shared by their agent", with the agent's own guess kept apart from the evidence. **Exists** (`backend/README.md`, "Sharing from chat").
- **Notes: anyone in the project.** Members and the leader add more detail, their own guess or a correction to any finding they can read. Notes reach the Super Brain and every agent with the finding. **Exists.**
- **Downward: access-checked lookup.** The backend fetches relevant findings and adds them to the agent's request; agents never query the Super Brain freely. **Exists.**
- **The leader** can comment on findings and add hypotheses (as notes, which exist), visible to that project's members. The leader can also request an investigation: it arrives in the researcher's workspace, the researcher accepts it, and any runs inside it still wait for **Run**.

---

## 11. VS Code extension (decision 6)

- **First version:**
  - reports terminal runs (command, log file, exit code) to the local runtime, so watching starts by itself;
  - shows alerts and proposed experiments as notifications that link to the dashboard.

  Watching only reads logs, so it keeps the no-automatic-runs rule. Chat, approvals and Run buttons stay in the dashboard.
- **Built so far:**
  - runs typed in the terminal are watched (by the decision at the top);
  - **▶ Run a simulation** starts an entry point the researcher picks and watches it, even without VS Code's terminal command reporting;
  - a status bar and pop-ups;
  - ExpandRG started in the background so a VS Code reload doesn't stop it.

  Learned on first use: terminal command reporting (shell integration) can be turned off in a researcher's VS Code, so the extension must not depend on it alone.
- **Later:** a chat panel and inline anomaly notes in the editor.
- File and log watching keep working without VS Code.

---

## 12. Risks and trade-offs

1. **CREAO may run code in its own cloud.** The old hub audit skill set out to "independently verify claims numerically", which suggests CREAO-side tools that can execute code. Local-only execution means configuring the CREAO agents **without** code-execution tools, and ExpandRG can't verify that from its side. **Action:** check the personal agents' tools in agent.creao.ai.
2. **Hidden instructions in data.** Code, logs and findings can carry text that tries to steer an agent. CREAO's plans are treated as untrusted; the policy engine and the Run click are the safeguards.
3. **Speed.** Investigations move at the speed of CREAO (10 to 90 s per run) and the researcher's clicks. The dashboard must work asynchronously.
4. **Credits.** Every investigation uses up to 2 CREAO runs, plus chat.
5. **Wrong or flaky experiments.** Environment differences, random seeds or numerical noise can make an experiment seem to confirm something it doesn't. Experiments record their environment, and the researcher confirms bugs.
6. **Not every anomaly is a bug.** Labels and the researcher's final say protect real scientific behaviour.
7. **Privacy vs usefulness.** More automatic context gives better answers but exposes more. That's why the tick boxes are per project, with conservative defaults.
8. **More to install.** The VS Code extension, and later Docker images with the right toolchain (Fortran, MPI). Each piece stays optional.

---

## 13. Build order

Each phase can be shown on its own and has its own checks. Nothing here starts before the previous phase works.

| Phase | What | How it is checked |
|---|---|---|
| 1 (**built**) | **Experiments without AI:** run manifest per project, experiment copies, process sandbox with limits and kill switch, **Run** / **Run all** in the dashboard, comparison with the original run | Limits stop a runaway run; the real project is byte-for-byte unchanged after experiments; a parameter change runs in a copy and its result shows next to the original |
| 2 | **Local checks:** physical rules per project, baseline runs, anomaly labels | Planted rule breaks fire; a run matching its baseline raises nothing |
| 3 | **Investigations:** orchestrator, CREAO plan as JSON (hypotheses + experiments), policy checks, privacy tick boxes, pause switch, send log, run cap | A planted blow-up becomes hypothesis → prepared experiment → (Run) → comparison → draft finding, using at most 2 CREAO runs; nothing outside the ticked kinds is sent |
| 4 | **Permanent changes:** approval queue, applied as a git branch or commit | Nothing is applied without approval; the applied change matches the approved one |
| 5 | **Leader:** comments, hypotheses, investigation requests accepted by the researcher | Only that project's members see comments; a request does nothing until accepted |
| 6 (moved up; **started**) | **VS Code extension, first version** | A terminal run is watched without picking its log; notifications link to the right page |
| Later | Containers, statistical detection, VS Code chat panel and inline notes | |
