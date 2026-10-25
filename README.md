# ExpandRG

**Expand Researching Globally.**

A collaborative AI research assistant for research groups that build custom numerical solvers. Each researcher has a personal agent that helps them understand their code, monitor simulation runs and investigate bugs. Findings a researcher approves go into the group's private shared knowledge base, the **Super Brain**, so the next researcher who hits a related problem can find them.

**Status:** prototype. The dashboard is built for laptops and desktop computers.

## CREAO agents

The core work is done by CREAO agents built on [agent.creao.ai](https://agent.creao.ai):

| Agent | What it does | Agent ID |
|---|---|---|
| **Super Brain** | The group's shared agent: answers from the group's approved findings, writes worksheets and onboarding lessons (a paper and its code, with equations and diagrams) | `0bb3ca8b-e123-401f-aa10-c291f1f55885` |
| **Personal agents** | One per researcher, private to them: project overviews, diagnoses, simulation setup, and the ExpandRG AI chat in VS Code | `2c814bb4-30ef-4da7-84f9-99f13cbed0e7` (Researcher B's) |

## Docs

- [Build plan](Documents/build-plan.md): ordered steps for the first milestone, with a checkpoint test after each
- [Dashboard design](Documents/dashboard-design.md): desktop layout and views
- [Demo script](Documents/demo-script.md): how to present ExpandRG on your own
- [Personal agent toolkit](Documents/agent-toolkit.md): what an agent can look at (your files, runs and the group's findings), the CREAO check, and setup in VS Code
- [Visualization Studio design](Documents/visualization-design.md): the **Visualize** page's graphs for comparing runs, edited by hand or by your own CREAO agent (phases 1 and 2 built)
- [Research Context Engine](Documents/research-context-engine.md): permission-checked run, anomaly, investigation, evidence and finding history, backed by existing local stores
- [Devpost write-up](Documents/devpost.md): the project in short, and what it's built with
- [Changelog](CHANGELOG.md): what each commit changed

## Folder map

| Folder | What goes there |
|---|---|
| `frontend/` | React dashboard |
| `backend/` | FastAPI backend: permissions, CREAO client, memory, monitoring, knowledge sharing, SQLite |
| `local_connector/` | Python process on the researcher's computer: authorized file access, evidence extraction, simulation monitoring |
| `expandrg/` | The installed package's `expandrg` command: the launcher, group setup, and the server for the built dashboard |
| `vscode-extension/` | The VS Code extension, which carries the package and installs it |
| `tests/` | Automated tests (CREAO is mocked, so tests use no credits) |
| `Documents/` | Build plan and dashboard design |

## Install on any computer

ExpandRG installs as one package: no copy of this folder, and no Node. It needs Python 3.10 or newer.

**With VS Code:**
1. Install the extension: `code --install-extension expandrg-0.16.0.vsix --force`, or in VS Code: Extensions → **…** → **Install from VSIX…**.
2. Open your project folder and click **ExpandRG: Set up** in the bottom bar. If no Python on the computer has ExpandRG yet, it offers to install it (the extension carries it) into the first Python 3.10+ it finds, or one you pick. It needs the internet once, for ExpandRG's own packages.
3. The group leader sets up the group once: **Set up a new group…** in the same list. Everyone then picks the group, who they are and the folder's project.

To keep ExpandRG's packages apart from your project's, give it its own virtual environment: `python -m venv %USERPROFILE%\.expandrg\venv` (`~/.expandrg/venv` on macOS and Linux), and pick its `python.exe` (`bin/python`) when Set up asks.

**Without VS Code:**
```
python -m pip install expandrg-0.16.0-py3-none-any.whl
expandrg setup                      # the group leader, once: the group and its CREAO agents
expandrg start --group <group-id>   # the dashboard is at http://127.0.0.1:5173
expandrg add-project                # another project for the group
expandrg stop
```

Installed, everything ExpandRG saves is in `~/.expandrg` (or `$EXPANDRG_HOME`): the groups with their keys and data, folder grants, experiments and logs. The demo group also works there if its CREAO keys are in `~/.expandrg/demo.env` (the same lines as `backend/.env.example`).

**Building the package** (from this folder; needs Node for the dashboard, `npm install` in `frontend/` and `vscode-extension/`):
```
python build_package.py          # dist/expandrg-<version>-py3-none-any.whl, also copied into vscode-extension/engine/
python build_package.py --vsix   # also tests the extension and packages vscode-extension/expandrg-<version>.vsix with the wheel inside
```
The build refuses a wheel that would contain keys or databases. Keep the version the same in `expandrg/__init__.py` and `vscode-extension/package.json`.

## Setup (from the source code)

1. Copy `backend/.env.example` to `backend/.env` and fill in the CREAO keys and app IDs (for the demo). Never commit `.env`. For your own group, use `python setup_expandrg.py` instead (see below).
2. Install the Python packages: `python -m pip install -r backend/requirements.txt`. If you use a virtual environment (VS Code may turn one on in its terminal), run this with that environment active, so the packages go into it.
3. Install the dashboard: `cd frontend` then `npm install`
4. Run the tests from the repo root (no network, no credits): `python -m pytest`
5. Check that CREAO works (this starts a real run and uses credits): `cd backend` then `python testcreo.py`
6. Optional: `python -m pip install -e .` gives that Python the `expandrg` command, running this folder's code. The data stays in `backend/data` and the keys in `backend/.env`, as with `run_demo.py`.

## Show it

```
python run_demo.py           # starts everything and opens Researcher A and Researcher B side by side
python run_demo.py --fresh   # the same, starting from clean demo data
```

Ctrl+C stops everything. The step-by-step demo for one presenter (what to click and what to say) is in [Documents/demo-script.md](Documents/demo-script.md).

## Set up your own research group

The demo group (Leader, Researchers A, B and C) is for presenting. Setup connects the Super Brain and personal agents you've already created in CREAO, and gives the new ExpandRG group its own empty research memory:

```
python setup_expandrg.py
```

It asks, in the terminal:
1. **Are you the group leader?** Only the leader sets up a group. If no, setup asks you to wait for your leader to provide the group ID and your sign-in name/member ID, then exits without asking for keys or creating anything.
2. The group's name and the leader's name.
3. **The Super Brain's** CREAO app ID and key, with where to find them: open the agent in agent.creao.ai, then its API Trigger settings. Copy the app ID (36 characters) and a key starting with `capi_`.
4. **Each researcher's** name and their personal agent's app ID and key. The leader enters them all.
5. The first project, and who works on it.

What it checks:
- Keys are typed hidden and checked with CREAO, at no cost; a refused key is asked for again.
- CREAO accepts any app ID on that free check, so the setup can test each app ID with one short run. It asks first, because that uses a little credit.
- Nothing is saved until you confirm.

Where it saves:
- On this computer only, in `~/.expandrg/groups/<group>/` (or `$EXPANDRG_HOME/groups/`):
  - `group.env`: the keys;
  - `group.json`: names and projects, no keys;
  - `data/`: the new Super Brain and private memory.
- The demo's data and `backend/.env` are left alone.

Start the group with `python run_demo.py --group <group>`. It prints a sign-in link for each person. Only one of the demo or a group runs at a time. More projects are added later (see [Use it on your own research project](#use-it-on-your-own-research-project)).

In VS Code, open the project folder and click **ExpandRG: Set up** in the bottom bar. It asks three things: your group, who you are, and which project the folder is. It saves them and starts that group. If the demo or another group is running, it offers to restart ExpandRG with yours (`python run_demo.py --stop` stops a running ExpandRG). Nothing has to be typed into settings files.

ExpandRG can't create CREAO agents itself: that needs a paid developer key (`cr_sk_`). Make the Super Brain and one agent per researcher in agent.creao.ai first.

## Use it on your own research project

Each project has its own updates, worksheets and runs, and only the researchers on it see it. To use ExpandRG on another project:

1. **The leader adds the project to the group**, either way:
   - on the dashboard: **Projects** → **New project**: its name, and who works on it;
   - or in the terminal: `python setup_expandrg.py --add-project`. It asks which group (when there's more than one), the project's name and who works on it.

   Both save it where VS Code looks, so nothing else needs editing.
2. **Each researcher on it** opens the project's folder in VS Code and clicks **ExpandRG: Set up** in the bottom bar, then picks the new project. The dashboard shows it after a reload.
3. Optional: **Project** → **Build overview** → paste the project's folder path into **Grant access** → **Map this folder**. Check exactly what will be sent, then **Send to my agent** (one CREAO run, about a minute). The overview is saved privately, and every later **Diagnose** on that project includes it automatically.

In the demo group, the demo leader's **Projects** page works the same way, or use `python -m backend.workspaces.demo --add-project my-research --name "My research" --members researcher_a`. VS Code's **Set up** lists only the demo's two built-in projects, so pick a new one with **ExpandRG: Choose project**.

From the command line instead:
```
python -m local_connector grant "C:\path\to\your\project"
python -m local_connector overview "C:\path\to\your\project" --as researcher_a --project my-research
```

## Analyze and export graphs

**Visualize** compares watched runs and experiments. Select a baseline for absolute/relative differences, choose a histogram or experiment parameter sweep, and save or undo graph changes. **Read full log** restores original measurements only when the granted source still matches the recorded run. Units come from the project’s `expandrg.json` or your explicit graph settings.

**Diagnose this problem** on a run alert prepares a diagnosis with private graphs attached. You click Send; the agent’s checked graph changes can be opened in Visualize and remain attached to the private draft.

For publication PDF/SVG/PNG with physical sizes, DPI, and font controls, install the optional extra in the Python environment that runs ExpandRG:

```
python -m pip install "expandrg[export]"
# From a local wheel:
python -m pip install "expandrg-0.16.0-py3-none-any.whl[export]"
```

Browser PNG/SVG export remains available without this extra. CREAO provides graph instructions; all measurements, calculations, and rendering stay in ExpandRG.

## Run the dashboard by hand

From the repo root, in three terminals:

```
python -m uvicorn backend.api.app:app --port 8000      # 1. the backend
python -m local_connector serve                        # 2. the local connector (reads your granted folders)
cd frontend && npm run dev                             # 3. the dashboard
```

Open http://127.0.0.1:5173 and choose a demo member. To show two researchers side by side, open http://127.0.0.1:5173/?as=researcher_a and http://127.0.0.1:5173/?as=researcher_b in two windows (add `&view=alerts` for Runs, or `&view=drafts` or `&view=superbrain`, to start on a view).

The researcher's **Simulations** page prepares and runs projects directly:

1. In **Set up & run**, choose an existing simulation project and click **Detect & prepare**. ExpandRG checks its `.venv`, `venv`, or `env`, then an active virtual/Conda environment, then its current Python. Your existing personal CREAO agent reads a redacted project map and prepares the project’s build steps, inputs, and run command. No `expandrg.json` or log file selection is required.
2. Review the saved setup and click **Run simulation**. The project uses its existing interpreter and installed dependencies; compiled languages such as Fortran build locally before execution. A failed build stops there. ExpandRG prepares existing simulations, without creating a new solver or environment by default. Unresolved tools or inputs block Run.
3. **Monitor** opens automatically with live metrics and alerts. The group leader sees it on their **Run results** page: the run's log and data drawn as graphs, with its problems marked. Other researchers cannot read your run details.
4. **Saved versions** keeps each version's code, inputs, setup, output, and results. In a version, **Tell your agent more about this version** (for example, answer its question, or say which make or how many threads), then **Update setup**. A version that already ran keeps its setup, and the update goes to a copy, the next version. **Prepare from this version** creates another copy for a new run, and **Delete this version** removes it from this computer (not while it runs; its results stay in Monitor). Editing the source project or preparing a new version never overwrites an earlier version. Your original project is only read.

For a custom location, open **Existing environment** before preparing, or **Environment details & connection** on a saved version. Enter an existing environment folder/Python executable or Fortran compiler path. **Use environment & update setup** asks CREAO to revise the plan; **Recheck existing tools** refreshes detection without another agent request. Compiler settings in `FC`, `CC`, `CXX`, Makefiles, and CMake presets/cache are reused. SDKs that need activation scripts should be activated in the terminal used to start ExpandRG. Discovery uses that execution environment; Windows and WSL are separate.

Versions live locally under `~/.expandrg/simulations/<group>/<id>/` (or `$EXPANDRG_HOME/simulations/`), with a retained snapshot and a separate execution copy. Versions retain their interpreter/toolchain selection, while environments remain at their original local paths and must stay available. The existing watcher APIs remain available for runs started outside this page. A supported compiler/build tool must be installed locally. Run has a 10-minute wall-time and 2-GB memory limit, with up to 50 MB of output per step; these processes are resource-limited, not isolated from the host filesystem or network.

In VS Code, the ExpandRG extension ([vscode-extension/](vscode-extension/README.md)) adds **Set up simulation** to the bottom bar. It opens a request in your personal agent chat: click **Send** for the agent to inspect permitted project files and help prepare the environment, build steps, input files, and run commands. Python and compiled languages such as Fortran use their existing project workflow. You review and run the proposed commands in your normal terminal; ExpandRG monitors the output, with pop-ups for problems and a dashboard link. Commands in `expandrg.json` are watched automatically, other recognized runs after one click, when VS Code's terminal command reporting is on. You can also watch a log file. The extension starts ExpandRG in the background if it isn't running (`run_demo.py --no-browser`).

The extension also has **ExpandRG AI**, a private chat powered by your own CREAO research agent. Use **Understand project** to map the folder, review what will be sent, then **Send to my agent**. The saved overview lets you discuss and refine the build and simulation setup, including Fortran compilation, input files, and runtime parameters. The settings icon opens **Agent instructions** to customize how your agent helps. Chat proposes commands; it does not execute them or edit your project. Install extension version **0.16.0** to see the updated panel (see [extension setup](vscode-extension/README.md)).

Runs made before ExpandRG, or outside it, can be added to **Monitor** and **Visualize** without running anything again: grant the run's folder, then `python -m local_connector import-runs <folder> --project <id> --as <member>`. It only reads the folder. The group's Fortran swimmer runs (`fort.10` positions, `fort.80` energy per beat, `setup.txt`) get distance from start, velocity (the centre's distance per beat) and efficiency (velocity² / power), as the group's `compare_v3` works them out; other runs get the numbers in their log and one-number-per-line output files. The dashboard no longer has an Experiments page: try a change as a new version under **Simulations → Set up & run**.

To compare runs on one graph, open **Visualize** (or **Open in Visualize** on a run). Tick the runs, then pick measures, the x axis, line or scatter, runs together or in separate panels, a log scale, a range of steps, a title and units. Under the graph are notes on anything it can't show, the largest difference between runs, and a table of the same numbers. **Save** keeps the graph (privately) and saves each later change, with **Undo**; PNG and SVG export are light on white. Researchers can also ask their own agent in the **Edit with Agent** panel ("use steps 400 to 900", "show residual instead", "these results look wrong"). The agent sees summaries and statistics, never the full data, and changes the graph only through operations ExpandRG checks; it never changes the runs, and only you set units. Each message uses CREAO credits. The group leader graphs the runs shared with them by hand.

To show offboarding, first run `python -m backend.workspaces.demo` once (it adds Researcher C's sample work history), then sign in as the Group Leader and use the Offboarding view on Researcher C. The handed-off work appears in the Super Brain marked "handed off at offboarding". Offboarding can't be undone except with `python -m backend.workspaces.demo --reset`, which starts the demo over.

## Ask the Super Brain

The chat is **CREAO plus your group's specialized research knowledge**. Researchers A and B keep their own personal agents for diagnoses and project work. When a researcher approves a finding for sharing, it enters the shared Super Brain database. Every chat message retrieves relevant approved records and sends them to the central CREAO hub, which combines those records with its own scientific knowledge and reasoning to produce one answer.

Open **Super Brain → Ask Super Brain**. By default the chat retrieves across **All my projects**, limited to those you can access; the Knowledge selector can narrow it to the current project. Try "How do Researcher A's and Researcher B's results relate?" or "Explain why refining the grid changes the stability limit." The answer can explain general concepts even when no specialized findings are available, while identifying what your group has actually observed. Shared findings retain their source author and hypothesis/verification labels.

There is one response path: every message goes to CREAO, including greetings and response-style requests. There is no separate lookup mode or locally generated substitute answer. Sending uses CREAO credits, and an agent run can still take tens of seconds.

You can say **Act as a researcher and be clear and concise.** The app saves standalone response instructions and includes them in the hub request, so CREAO can acknowledge and follow them. `/rules <instructions>` replaces preferences, `/rules show` asks about them, and `/rules reset` restores defaults. You can also use **Agent instructions → Save instructions**. Rules remain private per user and selected project and apply to later diagnoses and overviews too.

The last 12 completed exchanges are kept privately per project and knowledge scope. Recent conversation and up to eight selected approved records are supplied to the hub on each request; the records include their evidence and original agent run IDs. Private project overviews and unapproved drafts are not added to the hub's shared knowledge. **Clear chat** clears the selected local conversation and keeps instructions; it does not erase CREAO's own memory. Restarting the backend can interrupt tracking of in-progress jobs.

**Worksheets: the Super Brain as a teacher.** On **Super Brain → Worksheets**, the leader clicks **New worksheet**, ticks who it's for, and chooses how:
- **Ask the Super Brain:** optionally say what it should cover. Left empty, it teaches each person what the *others* found.
- **Write it myself:** type your own questions and "try it" steps (and model answers if you like), with an optional title.

Then **Send to CREAO**. One run (about a minute) writes one worksheet for each person picked: questions, and things to try in a copy of their project. A worksheet from your own questions is exactly those, in your order and words; CREAO adds the model answers you left out, a topic for each and the findings they rely on. To have CREAO add questions of its own, use Ask the Super Brain. Researchers type their answers and click **Send to the leader**. The leader reads them next to the model answers and writes comments, and the researcher then sees the comments and the model answers. Nothing is graded automatically, and a "try it" step never runs anything by itself.

Each person's worksheets are numbered **Worksheet 1, 2, 3**. By default a worksheet teaches only what's new to that person: findings shared, or corrected with a note, since their last worksheet, building on the topics they covered before. Untick "Only findings that are new" to cover everything again.

Any worksheet can be cancelled and restored later. A cancelled worksheet disappears for the researcher, its lessons come back in their next one, and that one takes its number.

**Lessons: learning a new project's code.** On **Super Brain → Lessons**, a researcher picks the paper and the code file built from it, both from folders they granted. The paper can be a PDF, its LaTeX source (`.tex`, which keeps the equations exact), or a Markdown or text copy; reading PDFs needs `python -m pip install "expandrg[papers]"`. The whole code file is read (up to about 1,200 lines), and a focus question is optional. What will be sent loads by itself (**See exactly what's sent**), then **Write the lesson** starts one Super Brain run (one to three minutes). The lesson has:
- the idea in plain words;
- the paper's equations, drawn, each with where it is in the paper;
- every term with its meaning, units and the variable that holds it;
- how the code does it, with the code beside the lesson (click any line reference to highlight it);
- pictures: a flow of the code's steps, stencil grids, and plots ExpandRG computes from the lesson's formulas;
- caveats and a few questions to check yourself.

ExpandRG checks the lesson before showing it. Cited lines must be lines that were sent, and names not in the code are struck through. The pictures are descriptions ExpandRG draws itself, never images or code from the agent. Every paper is checked on Crossref (only titles and DOIs are sent there): the paper you gave, papers the project cites itself, papers found, and papers that may be wrong. Lessons are private: the leader and other researchers can't see them, and no one is told.

Super Brain chat is tuned for speed. It asks CREAO for a fast model (`gemini-3-flash`), and asks the hub to answer directly, without its audit workflow, tools or skills: that workflow made replies take 20 to 60 seconds, even for "hello". Set `CREAO_CHAT_MODEL_ID=saved` in `backend/.env` to keep the agent's own model. For the fastest replies, make the dedicated chat agent in CREAO ([setup](Documents/super-brain-chat-agent.md)); it uses an ordinary `capi_` app key.

## Try it from the command line

From the repo root, in two terminals. The diagnosis starts a real CREAO run and uses credits.

```
# Terminal 1: start the backend
python -m uvicorn backend.api.app:app --port 8000

# Terminal 2: allow ExpandRG to read the sample project, then diagnose it as Researcher A
python -m local_connector grant tests/fixtures/sample_project
python -m local_connector diagnose tests/fixtures/sample_project --as researcher_a --project heat-diffusion --symptom "max|T| reaches inf at step 2223" --file run.log --file config.json --file heat2d.py:18-25

# Review the private draft, then share it (or reject it)
python -m local_connector drafts --as researcher_a
python -m local_connector approve <draft id> --as researcher_a

# Researcher B sees the shared finding
python -m local_connector findings --as researcher_b --project heat-diffusion

# Researcher B hits a related problem; their agent gets A's finding as context
python -m local_connector grant tests/fixtures/sample_project_b
python -m local_connector diagnose tests/fixtures/sample_project_b --as researcher_b --project heat-diffusion --symptom "I refined the grid to dx = 0.005. Now max|T| reaches inf at step 944, even though dt = 0.01 is below the dt = 0.02 we were told is safe." --file run.log --file config.json --file heat2d.py:18-25

# Search the Super Brain, or open one finding and its links
python -m local_connector ask "dt stability limit after refining the grid" --as researcher_b
python -m local_connector finding <finding id> --as researcher_b

# Watch a run log for anomalies (read-only; Ctrl+C stops watching, not the run)
python -m local_connector watch tests/fixtures/sample_project --file live.log --as researcher_a --project heat-diffusion
```

The connector shows exactly what will be sent and asks before sending. Each researcher's diagnosis runs on their own CREAO agent, and drafts stay private until approved. Granted folders are remembered in `~/.expandrg/grants.json`; take access back with `python -m local_connector revoke <folder>`. If the backend says the database is from an older version, run `python -m backend.workspaces.demo --reset`.
