# ExpandRG

**Expand Researching Globally.**

A collaborative AI research assistant for research groups that build custom numerical solvers. Each researcher has a personal agent that helps them understand their code, monitor simulation runs and investigate bugs. Findings a researcher approves go into the group's private shared knowledge base, the **Super Brain**, so the next researcher who hits a related problem can find them.

**Status:** prototype. The dashboard is built for laptops and desktop computers.

## Docs

- [Build plan](Documents/build-plan.md): ordered steps for the first milestone, with a checkpoint test after each
- [Dashboard design](Documents/dashboard-design.md): desktop layout and views
- [Demo script](Documents/demo-script.md): how to present ExpandRG on your own
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

1. Copy `backend/.env.example` to `backend/.env` and fill in the CREAO keys and app IDs. Never commit `.env`.
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

To watch a run live, start the sample solver slowly so you can watch it fail: `python tests/fixtures/sample_project/heat2d.py tests/fixtures/sample_project/config.json --delay 2 > tests/fixtures/sample_project/live.log`. Then, in the **Runs** page, pick `sample_project / live.log` and click **Start watching**. The local connector reads the log on your computer and sends the numbers from each step and any alerts, so the run shows live with its graphs and alerts. Earlier runs stay in the list to open or replay. You and your group leader can see your runs; other researchers can't. The log file itself never leaves your computer, and ExpandRG never stops a simulation.

To show offboarding, first run `python -m backend.workspaces.demo` once (it adds Researcher C's sample work history), then sign in as the Group Leader and use the Offboarding view on Researcher C. The handed-off work appears in the Super Brain marked "handed off at offboarding". Offboarding can't be undone except with `python -m backend.workspaces.demo --reset`, which starts the demo over.

## Ask the Super Brain

The chat is **CREAO plus your group's specialized research knowledge**. Researchers A and B keep their own personal agents for diagnoses and project work. When a researcher approves a finding for sharing, it enters the shared Super Brain database. Every chat message retrieves relevant approved records and sends them to the central CREAO hub, which combines those records with its own scientific knowledge and reasoning to produce one answer.

Open **Super Brain → Ask Super Brain**. By default the chat retrieves across **All my projects**, limited to those you can access; the Knowledge selector can narrow it to the current project. Try "How do Researcher A's and Researcher B's results relate?" or "Explain why refining the grid changes the stability limit." The answer can explain general concepts even when no specialized findings are available, while identifying what your group has actually observed. Shared findings retain their source author and hypothesis/verification labels.

There is one response path: every message goes to CREAO, including greetings and response-style requests. There is no separate lookup mode or locally generated substitute answer. Sending uses CREAO credits, and an agent run can still take tens of seconds.

You can say **Act as a researcher and be clear and concise.** The app saves standalone response instructions and includes them in the hub request, so CREAO can acknowledge and follow them. `/rules <instructions>` replaces preferences, `/rules show` asks about them, and `/rules reset` restores defaults. You can also use **Agent instructions → Save instructions**. Rules remain private per user and selected project and apply to later diagnoses and overviews too.

The last 12 completed exchanges are kept privately per project and knowledge scope. Recent conversation and up to eight selected approved records are supplied to the hub on each request; the records include their evidence and original agent run IDs. Private project overviews and unapproved drafts are not added to the hub's shared knowledge. **Clear chat** clears the selected local conversation and keeps instructions; it does not erase CREAO's own memory. Restarting the backend can interrupt tracking of in-progress jobs.

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
