# ExpandRG for VS Code

The VS Code side of ExpandRG. The extension is the face; the local connector (Python, in this repo) is the engine: it reads your runs, runs the checks and sends numbers and problems to the backend, which the web dashboard shows to you and your group leader. ExpandRG never stops a simulation on its own.

## First time: ExpandRG: Set up

The first time you open a project folder, ExpandRG asks to **Set up** (also in the bottom bar, and as the command **ExpandRG: Set up**). It takes about a minute and nothing is typed into settings files:

1. **A Python with ExpandRG**: found by itself (the configured one, then `python` / `py` on PATH). If none has it, the extension installs ExpandRG, which it carries, into the first Python 3.10+ it finds, or one you pick (for example a virtual environment's `python.exe`). pip's output is under **Output → ExpandRG**; it needs the internet once. If the installed ExpandRG is older than the one the extension carries, it offers to update it. No copy of ExpandRG's source is needed.
2. **Your group**: the groups made on this computer with `expandrg setup`, and the demo group. **Set up a new group…** (leader only) opens that setup in a terminal.
3. **Who you are** in that group.
4. **Which project this folder is**: only your projects are listed, and if there's one it's chosen for you. A project that isn't listed yet is added by the group leader (dashboard **Projects** → **New project**, or `expandrg add-project`); run **Set up** again afterwards.

A workspace set up with an ExpandRG source folder (`expandrg.connectorFolder`) keeps running that folder's `run_demo.py` while its Python doesn't have the package.

It saves these in the workspace settings, turns on terminal command reporting there so typed runs can be watched, and starts the group. If ExpandRG is already running another group, it asks before restarting it with yours. When it's done, it offers to open **ExpandRG AI** or the dashboard.

## Talk to your personal agent

Click **ExpandRG AI** in the bottom bar, or run **ExpandRG: Talk to ExpandRG AI** from the command palette. The panel uses a chat layout with slate and teal colors, clean typography, and a rounded composer, light and dark themes, and instructions behind the settings icon. The visible agent is ExpandRG AI, with a Powered by CREAO note. The panel opens beside your code, using the researcher and project configured for this workspace. Researcher A talks to A's own CREAO agent, and B talks to B's; this conversation is separate from Super Brain chat. The tab, chat header, welcome, and replies identify the personal agent as **Agent 1**, **Agent 2**, and so on. Numbers follow the order agents were registered/activated in the group; retired agents keep their place, and changing project or clearing a conversation does not renumber them. In the existing demo, A has Agent 1 and B has Agent 2. The agent is instructed to introduce itself by that name on the first reply of a new conversation.

- **Your agent can look at your files.** When your open folder is allowed, the panel header says `… · can read <folder>`, and the agent can list and read research files there, search the Super Brain and look at your runs before answering. Replies are formatted (headings, bold, lists, code and tables), never shown as raw Markdown. Under each reply, a small grey `Sources:` line names what the agent looked at. On your first message in a folder that isn't allowed yet, VS Code asks **Allow ExpandRG to read "<folder>"?** Only the folder's name goes to ExpandRG; `.env` files, keys and binary files are never read. See [the toolkit](https://github.com/Victor20120/CalHacks-2026/blob/main/Documents/agent-toolkit.md).
- **Understand project** maps a permitted workspace folder locally. Review the exact context in the panel, then click **Send to my agent**. Nothing goes to CREAO before that click. The saved private overview becomes context for later personal messages and diagnoses.
- The settings icon → **Agent instructions** lets you save how the agent should help you. You can also say "Act as a researcher and explain compiler flags clearly" or "Be conversational and answer directly" in chat. Simple questions default to short, natural replies without internal skill-loading narration. Preferences apply to future personal conversations, diagnoses, and overviews for this project; they are request instructions, not edits to the hosted CREAO skill.
- **Set up simulation** (the bottom-bar button or chat shortcut) prepares a setup request; click **Send** for your agent to inspect the project's permitted files. It checks README/build instructions, source entry points, dependencies, and inputs, then proposes commands for your terminal. Python gets environment/dependency guidance; Fortran and other compiled languages get separate build, link, and run steps. Missing choices are asked about. Existing drafts are preserved. This action does not execute commands or modify files, and does not require an `expandrg.json`.
- Conversation history is saved privately per researcher/project and restored when reopening. A pending personal reply can reconnect. **Clear chat** keeps the saved overview and instructions. Changing the configured researcher/project clears the panel's old context before loading the new conversation.

CREAO remains the brain and skills provider. Personal chat uses the researcher's existing agent and its model configuration, independently of Super Brain chat settings. Sending a message or approved project map uses CREAO credits. Leaders have no personal agent in this prototype and must choose a researcher profile.

## What you see

```
┌──────────────────────────────────────────────────────────────────────┐
│ ☰  ←  →        [ 🔍 your folder                 ]   ◄ command box     │
│                                                                      │
│                                     ┌───────────────────────────┐   │
│                                     │ ExpandRG is watching ...  │◄ pop-ups
│                                     └───────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│ ExpandRG: watching ... · 1 critical   Set up simulation   ◄ bottom bar │
└──────────────────────────────────────────────────────────────────────┘
```

- **Set up simulation** (bottom bar, also a command): open your personal agent with a setup request ready to review and send. After the agent explains the project's build and run steps, start the simulation yourself in the terminal. ExpandRG watches recognized runs or a log file you choose.
  - `{python}` in the manifest becomes `expandrg.pythonPath`.
  - Python output is not buffered, so lines appear as they are printed.
  - This works even when VS Code's terminal command reporting is off.
  - Closing that terminal stops the run, like any terminal. You start the run; ExpandRG itself never starts or stops one.
- **Runs you type in the terminal** are watched too, when VS Code's terminal command reporting (shell integration) is on:
  - a command that runs an `expandrg.json` entry point (e.g. `python heat2d.py config.json`) is watched automatically;
  - another command that looks like a run (`python ...`, `./program`, `mpirun ...`, `julia ...`, `Rscript ...`) gets one pop-up: **Watch this run?**;
  - everything else (`git`, `cd`, `ls`, ...) is ignored and its output is never read;
  - `cd C:\sim; python heat2d.py` and `cd sim && ./solver` count as the run in that folder;
  - if the output goes to a file (`> live.log`, `| tee run.log`), that file is watched instead.
- **Pop-ups** (bottom right): when watching starts, at the first Warning, at the first Critical, and when the run ends (with its exit code if it failed). Each has **Open dashboard**.
- **Status bar** (bottom left): what is being watched and its Critical problems, e.g. `ExpandRG: watching live.log · 2 critical` (red when there's a Critical problem). Click it to open the dashboard.
- **ExpandRG starts by itself.** Once set up, if ExpandRG isn't running, the extension starts it in the background (`python -m expandrg start --no-browser`, outside VS Code), so reloading or closing VS Code doesn't stop it. If it stops, a warning offers **Start ExpandRG**; a pop-up says when it's connected. If it can't start (for example the demo has no keys on this computer), the warning says why at once. Its logs are in `~/.expandrg/logs/` (`launcher.log` and one per service), or `.demo-logs/` in a source folder.
- **Output → ExpandRG** shows what the extension saw: start-up, whether ExpandRG is reachable, every terminal command and whether it was watched, ignored or asked about, and each run's result.

## Commands

Open the command box (the search box at the top middle of VS Code, or Ctrl+Shift+P), type `>` and then:

| Command | What it does |
|---|---|
| **ExpandRG: Talk to ExpandRG AI** | Open private CREAO chat, understand the project, and refine instructions |
| **ExpandRG: Set up simulation** | Prepare a project-specific setup request in personal chat |
| **ExpandRG: Watch a log file** | Pick a log file your simulation writes; the first time, it asks to allow the folder |
| **ExpandRG: Stop watching** | Stops watching a log file (the simulation keeps running) |
| **ExpandRG: Choose project** | Which ExpandRG project this folder belongs to |
| **ExpandRG: Open dashboard** | The Runs page for you and this project |
| **ExpandRG: Start ExpandRG** | Starts ExpandRG in the background if it isn't running |

## Settings

**ExpandRG: Set up** fills these in for you. To set them by hand, put them in the workspace's `.vscode/settings.json`:

```json
{
  "expandrg.pythonPath": "C:\\path\\to\\your-venv\\Scripts\\python.exe",
  "expandrg.group": "fluids-lab",
  "expandrg.member": "ana-ruiz",
  "expandrg.project": "channel-flow-solver",
  "terminal.integrated.shellIntegration.enabled": true
}
```

| Setting | Default | What it is |
|---|---|---|
| `expandrg.member` | `researcher_a` | Who you are (prototype sign-in) |
| `expandrg.project` | empty | The ExpandRG project; **Choose project** sets it |
| `expandrg.pythonPath` | `python` | The Python with ExpandRG installed (Set up installs it there). It also runs `{python}` entry points |
| `expandrg.group` | empty | A research group made with `expandrg setup`. When set, the extension starts that group (`--group`) instead of the demo. Set `expandrg.member` to your ID in it |
| `expandrg.connectorFolder` | empty | Only to run ExpandRG from a copy of its source (the folder with `run_demo.py`) instead of the installed package |
| `expandrg.watchTerminalRuns` | on | Watch runs typed in the VS Code terminal |
| `expandrg.backendUrl`, `expandrg.connectorUrl`, `expandrg.dashboardUrl` | `127.0.0.1` ports 8000, 8001, 5173 | Where the parts run |
| `terminal.integrated.shellIntegration.enabled` (VS Code's own) | on | Must be on for typed runs to be seen. Use **Watch a log file** if terminal reporting is unavailable |

## Install it

```
code --install-extension expandrg-0.16.0.vsix --force
```

To build it, from the ExpandRG folder (`npm install` done in `frontend/` and `vscode-extension/`):

```
python build_package.py --vsix               (builds ExpandRG's package, tests the extension, packs both into the .vsix)
```

The `.vsix` carries ExpandRG's package in `engine/`; that's what Set up installs. Without it (a plain `vsce package`), Set up can't install ExpandRG and says so.

Or in VS Code: open the **Extensions** sidebar (Ctrl+Shift+X), click **…** at its top right, choose **Install from VSIX…** and pick the `.vsix` file. After installing a new version, run `>Reload Window` from the command box. To remove it, uninstall "ExpandRG" from the Extensions sidebar.

## Or try it in a test window

Open the `vscode-extension` folder in VS Code, then **Run → Start Debugging** (the F5 key; Fn + F5 on most laptops). A second VS Code window opens with the extension loaded; open your project folder in that window.

## If nothing happens

- **Output → ExpandRG** says what the extension saw.
- "The running ExpandRG backend does not have personal chat": an older backend is still running. Stop it (`expandrg stop`, or `python run_demo.py --stop` in a source folder), click **Start ExpandRG**, then click **Refresh** in the chat. Reloading VS Code alone does not update an already-running backend. Keep existing data; no reset is needed.
- "ExpandRG isn't running": click **Start ExpandRG**, or run `expandrg start`. Don't run it in a VS Code terminal you are about to reload, since reloading stops it.
- "ExpandRG didn't start: …": the rest of the message is ExpandRG's reason, for example "ExpandRG is already running (group fluids-lab)" or that the demo has no keys on this computer. **Show details** shows the end of `launcher.log`.
- A typed run isn't noticed: VS Code's terminal command reporting is off (`terminal.integrated.shellIntegration.enabled`). Turn the setting on, or use **ExpandRG: Watch a log file**.
- PowerShell shows `>>` instead of running a command: the line ends with a `` ` `` (often copied by accident), which tells PowerShell the command continues. Press Ctrl+C and retype it.
