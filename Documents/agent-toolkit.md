# Personal agent toolkit

What a researcher's personal agent can look at while it works on a simulation problem: their files, their runs and the group's findings. ExpandRG checks every request with the researcher's own permissions. Reads remain read-only; the Context Engine also accepts private, unverified relationship proposals.

## The tools

| Tool | What the agent gets | Where it runs | Permission |
|---|---|---|---|
| `list_project_files` | The research files in the researcher's granted project folder, or in one subfolder (`under`): code, build files, configs, data and logs. More than 150 come back as the folders that hold them, with how many each has | Local connector (`local_connector/agent_tools.py`) | Only a folder the researcher granted; sensitive, binary and tool-folder files, virtual environments (any folder with `pyvenv.cfg`) and hidden folders are never listed |
| `read_project_file` | One file with line numbers: a range (`"18-40"`), or the start of the file. A log without a range gives its first lines, the lines around problems and its end | Local connector | Same grant; at most 400 lines and 12,000 characters per request; secrets blanked |
| `search_super_brain` | The group's shared findings that match: title, project, researcher, category, whether a researcher verified it, summary with evidence, the agent's guess if any, and members' notes | Backend (`search_findings`) | Only projects the researcher belongs to, so never another group's or a restricted project's findings |
| `get_simulation_runs` | The researcher's recent watched runs with alert counts; with a `run_id`, that run's alerts and its numbers per step (range, first non-finite step, about 40 points) | Backend (runs store) | Only the researcher's own runs (the leader sees runs in their projects) |

The [Research Context Engine](research-context-engine.md) adds `get_research_context` (typed run, anomaly, investigation, finding, graph or experiment references), `find_related_experiments` (explicit recorded parameter values and tolerance), and `propose_research_relationship` (a private proposal that cannot verify or publish). Context is bounded and carries provenance and completeness labels. Personal chat uses audience-safe publication context because eligible results can auto-share as hypotheses; unpublished investigation prose and evidence content stay private. Direct authenticated tool requests and graph analysis can inspect the caller's private context.

Readable file types: Python, Fortran (`.f`, `.f90`, …), MATLAB (`.m`), Julia, R, C/C++/CUDA, Rust, Go, Java, shell, Makefile and CMake files, JSON, YAML, TOML, INI and namelist configs, CSV, TSV and `.dat` data, logs (`.log`, `.out`, `.err`, `.txt`), Markdown and LaTeX.

Never read, whatever the agent asks:
- `.env` files, keys and credentials, and anything inside `.git`, `.ssh` or `.aws`;
- binary files and unknown file types;
- anything outside the granted folder: `..`, local paths, and links (symlinks and Windows junctions) that lead elsewhere;
- a folder whose name two granted folders share.

Reading never runs anything: a file is only opened for reading.

## How the agent uses them

CREAO can't call tools that ExpandRG defines (see below), so the agent asks in its reply and ExpandRG does the work:

```
You ask Agent 1 in VS Code: "why does heat2d blow up?"
  → CREAO run 1. The agent replies with only a request block:
      {"tool_requests": [{"tool": "read_project_file", "path": "config.json"},
                         {"tool": "search_super_brain", "query": "time step stability"}]}
  → ExpandRG runs each request for you: files through your local connector, from your granted folder;
    searches and runs through the backend, with your project memberships
  → CREAO run 2. The agent gets tool_results and either answers or asks once more
  → Under the answer, in small grey text: "Sources: config.json · run.log · heat2d.py · Super Brain (1 finding)"
```

Limits:
- 5 requests per round and 2 rounds per message, so a message takes at most 3 CREAO runs;
- 30,000 characters of results per round;
- unknown tools and refused files come back to the agent as errors, never as files.

Each round adds a CREAO run, typically 20 to 40 s.

Under the reply, `Sources:` names what the agent looked at: files by name (the whole path only when two share a name), runs and Super Brain findings. `Couldn't open:` names any file it asked for that wasn't there. A wrong path comes back to the agent with the files that have that name, so it can read the right one in its next round. Chat history keeps a summary of each request (why one failed, too), not the file contents.

Granting the folder is the permission (the group's choice, 2026-10-24): there is no extra prompt per file.

The agent is also told never to execute, compile or test the project's code in its own sandbox. Code runs only on the researcher's computer, when they click **Run**.

The backend entry point is `POST /agent-tools` (sign-in required; body `project_id`, optional `folder`, `tool` and `args`). Chat uses the same code, and an MCP server can wrap it later.

## CREAO: what was checked (2026-10-24)

The goal was to give the agents real MCP tools. That isn't possible with CREAO today:

- **No custom tools in API runs.** Asked directly in an API-triggered run (`a8d5e5ca`), Agent 1 listed only CREAO's own tools, said that no tool from the calling application is registered, and reported `connectors: []` for itself.
- **Custom MCP servers** in CREAO's docs are for the build agent, or a listed set of services connected with OAuth. They aren't for the agents that ExpandRG triggers through the API.
- **Device Connect**, which pairs a computer that can serve a custom MCP server: the docs don't say which operating systems it supports or whether it works on API-triggered runs. Agent 1 reports no device link. It would also give CREAO shell access and write access to files, which breaks the read-only rule.
- **CREAO can't reach the researcher's computer:** webhooks must be public URLs, and "URLs pointing to private networks are rejected".
- **⚠ The agents can run code in CREAO's cloud.** In that same run, Agent 1 ran `python3 -c "print(6*7)"` (output 42) and fetched `https://example.com` (HTTP 200). That breaks principle 3 of the [runtime design](agent-runtime-design.md) ("no cloud execution"). ExpandRG instructs the agents never to run project code, but that's only an instruction. Turn off code execution for Agent 1 and Agent 2 in agent.creao.ai if CREAO allows it.

If CREAO adds custom tools for API runs, or Device Connect proves to work on Windows, the tools can be served as MCP tools through `POST /agent-tools` without changing their rules.

## Setup on Windows with VS Code

1. Install the extension: `code --install-extension vscode-extension/expandrg-0.16.0.vsix --force`, then run **Developer: Reload Window**. **ExpandRG: Set up** installs ExpandRG itself if needed and starts it (or run `expandrg start`, or `python run_demo.py` in the ExpandRG folder). The backend reaches your local connector at `http://127.0.0.1:8001`; set `EXPANDRG_CONNECTOR` to change that.
2. Pick your group, who you are and the folder's project when Set up asks.
3. Open your project folder in VS Code and click **ExpandRG AI** in the bottom bar.
4. On your first message, if the folder isn't allowed yet, VS Code asks **Allow ExpandRG to read "<folder>"?** Click **Allow**. The panel header then says `… · can read <folder>`. If you click away, the agent works without files and says so.
5. Ask about your simulation. The agent decides what to read, and the `Sources:` line under its answer shows what it looked at.

Only the folder's name goes to the backend, never its path. Only the excerpts the agent reads go to CREAO, with secrets blanked.

## Demo (real CREAO, 2026-10-24)

The demo used scratch data on test ports 8100 and 8101, the sample heat project granted as `sample_project`, and Researcher A's verified finding "Explicit time step above the CFL limit makes the heat solver blow up", approved through the normal approval endpoint.

Question to Agent 1: *"My heat2d run blows up to inf after about 2000 steps. What in the code and config causes it, and has anyone in the group seen this before?"*

- **What it requested, in 2 rounds:**
  - it listed the folder's 4 files and searched the Super Brain (1 finding);
  - it checked runs (none) and read `config.json`, `heat2d.py`, `run.log` and `expandrg.json`.
- **Cost:** 3 CREAO runs, 72 s in CREAO.
- **The answer:**
  - It took `dt = 0.03`, `dx = 0.01` and `alpha = 0.001` from `config.json`, and `r = alpha*dt/dx**2` from `heat2d.py` line 32, giving r = 0.30, above the 2D limit of 0.25.
  - It checked the growth factor against `run.log`: about 1.4 per step, from 2.77e2 at step 100 to 2.75e14 at step 200.
  - It cited Researcher A's verified fix (`dt = 0.02`).
  - It suggested, without applying it, a guard after line 32 that stops the run when r > 0.25.

An earlier run, before the agent was told plainly that it can read the folder, answered from the finding alone and said it couldn't see the files. Both fixes went into the prompt: the agent is told the connected folder is readable, and `output_format` asks for either only the requests or the final answer.
