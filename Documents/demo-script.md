# ExpandRG — Demo Script (one presenter)

You play everyone: **Researcher A**, **Researcher B** and the **Group Leader**. Each person is a separate browser window, and the name in the top-right corner of the dashboard shows who you are in that window. Saying "I'm Researcher A now" as you move to that window is enough for the audience to follow.

**Length:** about 6–7 minutes live.
**Cost:** each full run-through makes 2 real CREAO runs (one for A, one for B), so rehearsals use credits too.

---

## Before you present (10 minutes)

1. Fill in `backend/.env` (all CREAO keys), and run `npm install` in `frontend/` once.
2. Start from a clean state: `python run_demo.py --fresh`
   - This starts everything, grants the two sample project folders, and opens two windows: **Researcher A** (Diagnose) and **Researcher B** (Super Brain).
   - Put them side by side: click a window, then press **Windows key + Left arrow**; click the other, **Windows key + Right arrow**.
3. Open a terminal in the repo folder and type this command, but don't press Enter yet (it's for the alerts part):
   ```
   python tests/fixtures/sample_project/heat2d.py tests/fixtures/sample_project/config.json --delay 2 > tests/fixtures/sample_project/live.log
   ```
4. Copy the two symptoms below into a notepad so you can paste them.
5. **Make a backup recording** of one full run-through (Windows: **Windows key + Alt + R** starts and stops screen recording with Xbox Game Bar). If the wifi or CREAO is slow on the day, you can play it.

**Symptom for Researcher A:**
```
The simulation blows up: max|T| reaches inf at step 2223 and the run stops. Expected the temperature to decay smoothly from 400 toward 0.
```

**Symptom for Researcher B:**
```
I refined the grid to dx = 0.005 for better resolution. Now the run blows up: max|T| reaches inf at step 944. dt = 0.01 is already half the dt = 0.02 we were told is safe.
```

---

## The demo

### 1. The problem (30 seconds)

> "In research groups that write their own solvers, everyone debugs alone. When someone figures out why a simulation blew up, that knowledge stays in their head, or leaves with them when they graduate. ExpandRG gives every researcher their own AI agent, and a private, shared group memory we call the Super Brain."

### 2. Researcher A hits a problem (1 minute) — left window

> "I'm Researcher A. My heat solver just blew up."

1. In **Diagnose**, the folder is already `sample_project`. Tick `config.json`, `heat2d.py` and `run.log`. Next to `heat2d.py`, type lines `18-25`.
2. Paste A's symptom. Leave Focus on *numerical*.
3. Click **Preview what will be sent**.

> "Nothing leaves my computer until I've seen it. Only these excerpts are sent: no whole repo, no `.env`, no private keys, and any secrets are blanked out."

4. Click **Send to my agent**. It takes about a minute.

### 3. While the agent works: live monitoring (1 minute) — left window

> "While my agent works, ExpandRG also watches my running simulations."

1. In the terminal, press Enter to start the solver (it runs slowly on purpose).
2. In A's window, click **Runs**, pick `sample_project / live.log`, and click **Start watching**.
3. The run appears under **Live** and its graphs grow as the solver writes. Within seconds: **Warning** (sudden jump at step 200), then **Critical** (overflow at step 800), marked on the graphs and listed under them.

> "These are cheap local checks, no AI needed. The Warning came 600 steps before the crash. And ExpandRG never stops my simulation, even on a Critical alert. It only reads the log."

4. Click **Diagnose** to go back to the result.

### 4. A's result, and sharing it (1 minute) — left window

> "My agent found the cause: the time step breaks the stability limit, r = 0.30, above 0.25."

- Point out the labels: **"agent: confirmed"** is the agent's opinion, *not* verified by a researcher.
- Click **Review the draft**. Show that you can edit it, and that it starts as a **Hypothesis**.

> "It's saved privately first. It only goes to the group when I approve it."

- Click **Share with everyone in Heat diffusion solver**.

### 5. Researcher B hits a related problem (1.5 minutes) — right window

> "Now I'm Researcher B, on the same project. I refined my grid and my run blew up too. And I was told dt = 0.02 is safe."

1. Click **Super Brain**: A's finding is already there under **Researcher A**, marked *Hypothesis · not verified*.
2. Click **Diagnose**. Choose the folder `sample_project_b`, tick the same three files, lines `18-25`, paste B's symptom, then **Preview** and **Send to my agent**.
3. Point at **Retrieved from the Super Brain**: A's finding was given to B's agent automatically.

> "My agent gets my teammate's approved finding as context, only because I'm in the same project."

4. When the result arrives:

> "The advice 'dt = 0.02 is safe' was right for A's grid, not mine: the limit scales with dx squared. Exactly the trap the group already learned about."

5. **Review the draft**, then **Share**. In **Super Brain**, open B's finding: it's **linked** to A's.

### 6. Privacy (30 seconds) — right window

> "Researcher B isn't in our wave-propagation project. B can't search it, open it, or follow a link into it, and the Super Brain never feeds B's agent anything B couldn't read."

(In the top bar, B's **Project** list only shows Heat diffusion solver.)

### 7. When someone leaves (1 minute) — new window

1. Open `http://127.0.0.1:5173/?as=leader&view=offboarding` (the launcher printed this link).

> "I'm the Group Leader. Researcher C is graduating."

2. Point at the policy at the top: it's disclosed to everyone in advance.
3. Click **Offboard** next to Researcher C and confirm.

> "C's access ends now. Their eligible work goes to the Super Brain and is verified, and their agent is retired. Personal notes, drafts they rejected and their agent conversations are left out."

4. Show the ✓ steps: 3 handed off, 2 left out.
5. Back in B's window, refresh **Super Brain**: C's work is there, marked **handed off at offboarding**.
6. In the leader's window, click **Super Brain**: the bar chart shows how many updates each researcher shared, and each researcher's updates are listed with their issue underneath.

> "As the leader I see what everyone shared, in one place. For more detail I just ask the Super Brain at the bottom of the page."

7. Type `blows up after refining the grid` in **Ask Super Brain** and press **Ask**.
8. Click **Runs**: A's run is in the list with its alerts. Click it, then **Replay**.

> "I see every run my researchers watch, live or afterwards. Here's A's run again, step by step: the Warning at step 200 and the Critical at step 800, long before the crash. The log file never left A's computer."

### 8. Close (30 seconds)

> "Every researcher has their own agent, the group keeps what it learns, and access rules are enforced in code, not left to the AI. Everything you saw ran on real AI agents, with over 200 automated tests behind it."

---

## If something goes wrong

| Problem | What to do |
|---|---|
| "Start the local connector" message | The launcher isn't running. Run `python run_demo.py` again |
| An agent run takes very long | It gives up after 5 minutes. Keep talking, or switch to your backup recording |
| "Database from an older version" | `python run_demo.py --fresh` |
| You need to start over (for example, after a rehearsal) | Ctrl+C the launcher, then `python run_demo.py --fresh`. Offboarding can only be undone this way |

## Shorter version (no waiting on stage)

Before presenting, do steps 2 and 4 (A's diagnosis and sharing) yourself. On stage, start at step 5: show A's finding already in the Super Brain, then do B live. That's one CREAO run on stage instead of two.
