# ExpandRG

**Expand Researching Globally.**

A collaborative AI research assistant for research groups that build custom numerical solvers. Each researcher has a personal agent that helps them understand their code, monitor simulation runs and investigate bugs. Findings a researcher approves go into the group's private shared knowledge base, the **Super Brain**, so the next researcher who hits a related problem can find them.

**Status:** prototype. The dashboard is built for laptops and desktop computers.

## Docs

- [Build plan](Documents/build-plan.md): ordered steps for the first milestone, with a checkpoint test after each
- [Dashboard design](Documents/dashboard-design.md): desktop layout and views
- [Demo script](Documents/demo-script.md): how to present ExpandRG on your own
- [Personal agent toolkit](Documents/agent-toolkit.md): what an agent can look at (your files, runs and the group's findings), the CREAO check, and setup in VS Code
- [Changelog](CHANGELOG.md): what each commit changed

## Folder map

| Folder | What goes there |
|---|---|
| `frontend/` | React dashboard |
| `backend/` | FastAPI backend: permissions, CREAO client, memory, monitoring, knowledge sharing, SQLite |
| `local_connector/` | Python process on the researcher's computer: authorized file access, evidence extraction, simulation monitoring |
| `tests/` | Automated tests (CREAO is mocked, so tests use no credits) |
| `Documents/` | Build plan and dashboard design |

## Setup

1. Copy `backend/.env.example` to `backend/.env` and fill in the CREAO keys and app IDs (for the demo). Never commit `.env`. For your own group, use `python setup_expandrg.py` instead (see below).
2. Install the Python packages: `python -m pip install -r backend/requirements.txt`. If you use a virtual environment (VS Code may turn one on in its terminal), run this with that environment active, so the packages go into it.
3. Install the dashboard: `cd frontend` then `npm install`
4. Run the tests from the repo root (no network, no credits): `python -m pytest`
5. Check that CREAO works (this starts a real run and uses credits): `cd backend` then `python testcreo.py`

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

Start the group with `python run_demo.py --group <group>`. It prints a sign-in link for each person. Only one of the demo or a group runs at a time.

In VS Code, open the project folder and click **ExpandRG: Set up** in the bottom bar. It asks three things: your group, who you are, and which project the folder is. It saves them and starts that group. If the demo or another group is running, it offers to restart ExpandRG with yours (`python run_demo.py --stop` stops a running ExpandRG). Nothing has to be typed into settings files.

ExpandRG can't create CREAO agents itself: that needs a paid developer key (`cr_sk_`). Make the Super Brain and one agent per researcher in agent.creao.ai first.

## Use it on your own research project

1. Add your project to the workspace (until ExpandRG is packaged with your own identity, you work as Researcher A):
   ```
   python -m backend.workspaces.demo --add-project my-research --name "My research" --members researcher_a
   ```
2. Run `python run_demo.py`, and in Researcher A's window pick **My research** in the **Project** list at the top.
3. **Project** → **Build overview** → paste your project's folder path into **Grant access** → **Map this folder**. Check exactly what will be sent, then **Send to my agent** (one CREAO run, about a minute).
4. The overview is saved privately, and every later **Diagnose** on that project includes it automatically.

From the command line instead:
```
python -m local_connector grant "C:\path\to\your\project"
python -m local_connector overview "C:\path\to\your\project" --as researcher_a --project my-research
```

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
4. **Saved versions** keeps each version's code, inputs, setup, output, and results. **Prepare from this version** creates another copy for a new run. Editing the source project or preparing a new version never overwrites an earlier version. Your original project is only read.

For a custom location, open **Existing environment** before preparing, or **Environment details & connection** on a saved version. Enter an existing environment folder/Python executable or Fortran compiler path. **Use environment & update setup** asks CREAO to revise the plan; **Recheck existing tools** refreshes detection without another agent request. Compiler settings in `FC`, `CC`, `CXX`, Makefiles, and CMake presets/cache are reused. SDKs that need activation scripts should be activated in the terminal used to start ExpandRG. Discovery uses that execution environment; Windows and WSL are separate.

Versions live locally under `~/.expandrg/simulations/<group>/<id>/` (or `$EXPANDRG_HOME/simulations/`), with a retained snapshot and a separate execution copy. Versions retain their interpreter/toolchain selection, while environments remain at their original local paths and must stay available. The existing watcher APIs remain available for runs started outside this page. A supported compiler/build tool must be installed locally. Run has a 10-minute wall-time and 2-GB memory limit, with up to 50 MB of output per step; these processes are resource-limited, not isolated from the host filesystem or network.

In VS Code, the ExpandRG extension ([vscode-extension/](vscode-extension/README.md)) adds **Set up simulation** to the bottom bar. It opens a request in your personal agent chat: click **Send** for the agent to inspect permitted project files and help prepare the environment, build steps, input files, and run commands. Python and compiled languages such as Fortran use their existing project workflow. You review and run the proposed commands in your normal terminal; ExpandRG monitors the output, with pop-ups for problems and a dashboard link. Commands in `expandrg.json` are watched automatically, other recognized runs after one click, when VS Code's terminal command reporting is on. You can also watch a log file. The extension starts ExpandRG in the background if it isn't running (`run_demo.py --no-browser`).

The extension also has **ExpandRG AI**, a private chat powered by your own CREAO research agent. Use **Understand project** to map the folder, review what will be sent, then **Send to my agent**. The saved overview lets you discuss and refine the build and simulation setup, including Fortran compilation, input files, and runtime parameters. The settings icon opens **Agent instructions** to customize how your agent helps. Chat proposes commands; it does not execute them or edit your project. Install extension version **0.10.0** to see the updated panel (see [extension setup](vscode-extension/README.md)).

To try a change without touching your project, open **Experiments**. A project needs an `expandrg.json` run manifest saying which commands experiments may run, which config keys they may change, and their time, memory and output limits (the sample projects have one). Pick the folder, change a parameter (for example `dt`) for up to three experiments, and click **Prepare**: that only makes copies under `~/.expandrg/experiments/`. Nothing runs until you click **Run** or **Run all**. Each run is stopped if it passes its limits, and the results are compared with the original run (`run.log`) in charts and a table. Experiments stay on your computer.

To show offboarding, first run `python -m backend.workspaces.demo` once (it adds Researcher C's sample work history), then sign in as the Group Leader and use the Offboarding view on Researcher C. The handed-off work appears in the Super Brain marked "handed off at offboarding". Offboarding can't be undone except with `python -m backend.workspaces.demo --reset`, which starts the demo over.

## Ask the Super Brain

The chat is **CREAO plus your group's specialized research knowledge**. Researchers A and B keep their own personal agents for diagnoses and project work. When a researcher approves a finding for sharing, it enters the shared Super Brain database. Every chat message retrieves relevant approved records and sends them to the central CREAO hub, which combines those records with its own scientific knowledge and reasoning to produce one answer.

Open **Super Brain → Ask Super Brain**. By default the chat retrieves across **All my projects**, limited to those you can access; the Knowledge selector can narrow it to the current project. Try "How do Researcher A's and Researcher B's results relate?" or "Explain why refining the grid changes the stability limit." The answer can explain general concepts even when no specialized findings are available, while identifying what your group has actually observed. Shared findings retain their source author and hypothesis/verification labels.

There is one response path: every message goes to CREAO, including greetings and response-style requests. There is no separate lookup mode or locally generated substitute answer. Sending uses CREAO credits, and an agent run can still take tens of seconds.

You can say **Act as a researcher and be clear and concise.** The app saves standalone response instructions and includes them in the hub request, so CREAO can acknowledge and follow them. `/rules <instructions>` replaces preferences, `/rules show` asks about them, and `/rules reset` restores defaults. You can also use **Agent instructions → Save instructions**. Rules remain private per user and selected project and apply to later diagnoses and overviews too.

The last 12 completed exchanges are kept privately per project and knowledge scope. Recent conversation and up to eight selected approved records are supplied to the hub on each request; the records include their evidence and original agent run IDs. Private project overviews and unapproved drafts are not added to the hub's shared knowledge. **Clear chat** clears the selected local conversation and keeps instructions; it does not erase CREAO's own memory. Restarting the backend can interrupt tracking of in-progress jobs.

**Worksheets: the Super Brain as a teacher.** On **Super Brain → Worksheets**, the leader clicks **Make worksheets**. One Super Brain run (about a minute) reads every finding shared in the project, sorts them into topics, and writes each researcher a worksheet from what the *others* found: questions, and things to try in a copy of their project. Researchers type their answers and click **Send to the leader**. The leader reads them next to the model answers and writes comments, and the researcher then sees the comments and the model answers. Nothing is graded automatically, and a "try it" step never runs anything by itself.

Worksheets come **weekly**. Each **Make week N worksheets** teaches each researcher only what's new to them: findings shared, or corrected with a note, since their last worksheet. It builds on the topics they covered before rather than repeating them. Untick "Only what's new" to cover everything again.

Who gets one is optional: tick the people (anyone with nothing new is greyed out). Any week can be cancelled (**Cancel week**), or one person's worksheet in it (**Cancel**), and restored later. A cancelled worksheet disappears for the researcher, and its lessons come back in their next week. A cancelled week stays on the leader's page, and its number is used again: cancel week 1 and the next one made is week 1.

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
