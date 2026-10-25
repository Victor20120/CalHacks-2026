// ExpandRG in VS Code. The extension is the face; the local connector (Python) is the
// engine: it reads the runs, runs the checks and sends numbers and problems to the
// backend, which the web dashboard shows. The extension never starts, pauses or stops
// a simulation.

import { ChildProcess, spawn, spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { call, HttpError, isUp } from "./http";
import { dashboardUrl, insideFolder, statusText, Watched } from "./logic";
import { watchTerminalRuns } from "./runs";
import { PersonalAgentPanel } from "./personalAgent";
import {
  bundledEngine, Engine, engineCommand, Group, isExpandrgFolder, listGroups, projectsFor, pythonCandidates,
  pythonVersion, supportedPython, wheelVersion,
} from "./setup";

const POLL_MS = 3000;
let connectorProcess: ChildProcess | undefined;
let output: vscode.OutputChannel;
let extensionPath = "";

function settings() {
  const c = vscode.workspace.getConfiguration("expandrg");
  return {
    member: c.get<string>("member", "researcher_a"),
    project: c.get<string>("project", ""),
    backendUrl: c.get<string>("backendUrl", "http://127.0.0.1:8000"),
    connectorUrl: c.get<string>("connectorUrl", "http://127.0.0.1:8001"),
    dashboardUrl: c.get<string>("dashboardUrl", "http://127.0.0.1:5173"),
    connectorFolder: c.get<string>("connectorFolder", ""),
    group: c.get<string>("group", ""),
    pythonPath: c.get<string>("pythonPath", "python"),
    watchTerminalRuns: c.get<boolean>("watchTerminalRuns", true),
  };
}

export async function activate(context: vscode.ExtensionContext) {
  extensionPath = context.extensionPath;
  output = vscode.window.createOutputChannel("ExpandRG");
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "expandrg.openDashboard";
  status.tooltip = "ExpandRG: open the dashboard";
  status.text = "ExpandRG";
  status.show();
  const setupButton = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 49);
  setupButton.text = "$(tools) Set up simulation";
  setupButton.tooltip = "Ask your personal agent to inspect this project and help prepare its build and run commands.";
  setupButton.command = "expandrg.setupSimulation";
  setupButton.show();
  const personalAgent = new PersonalAgentPanel({ settings, ensureProject, pickFolder, ensureGranted, chatFolder });
  const chatButton = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 48);
  chatButton.text = "$(comment-discussion) ExpandRG AI";
  chatButton.tooltip = "Talk to ExpandRG AI, powered by CREAO. Understand this project and refine your simulation setup.";
  chatButton.command = "expandrg.openPersonalAgent";
  chatButton.show();

  context.subscriptions.push(
    output,
    status,
    setupButton,
    personalAgent,
    chatButton,
    vscode.commands.registerCommand("expandrg.openPersonalAgent", () => personalAgent.show()),
    vscode.commands.registerCommand("expandrg.openDashboard", openDashboard),
    vscode.commands.registerCommand("expandrg.chooseProject", chooseProject),
    vscode.commands.registerCommand("expandrg.watchLogFile", watchLogFile),
    vscode.commands.registerCommand("expandrg.stopWatching", stopWatching),
    vscode.commands.registerCommand("expandrg.startConnector", () => startConnector(true)),
    vscode.commands.registerCommand("expandrg.setupSimulation", () => personalAgent.showSimulationSetup()),
    vscode.commands.registerCommand("expandrg.setUp", setUp),
  );
  if (!isConfigured() && vscode.workspace.workspaceFolders?.length) {
    vscode.window.showInformationMessage(
      "Welcome to ExpandRG. Set up your group, who you are and this folder's project: about a minute, nothing to type into settings.",
      "Set up", "Later",
    ).then((choice) => { if (choice === "Set up") void setUp(); });
  }
  const s = settings();
  output.appendLine(`ExpandRG ${context.extension.packageJSON.version} started as ${s.member}` +
    `${s.project ? ` in project ${s.project}` : " (no project chosen yet)"}; watching terminal runs: ${s.watchTerminalRuns ? "on" : "off"}.`);

  watchTerminalRuns(context, {
    settings,
    ensureProject,
    ensureGranted,
    openDashboard,
    log: (line) => output.appendLine(line),
  });

  await startConnector(false);
  const refresh = () => refreshStatus(status).catch((e) => output.appendLine(`Status: ${e}`));
  refresh();
  const timer = setInterval(refresh, POLL_MS);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
}

export function deactivate() {
  // ExpandRG keeps running when VS Code reloads or closes; stop it with Ctrl+C where it
  // was started, or by ending its python process.
}

// --- Status bar ----------------------------------------------------------------

let wasUp: boolean | undefined;

async function refreshStatus(status: vscode.StatusBarItem) {
  const s = settings();
  if (!isConfigured()) {
    status.text = "$(gear) ExpandRG: Set up";
    status.command = "expandrg.setUp";
    status.backgroundColor = undefined;
    return;
  }
  status.command = "expandrg.openDashboard";
  const up = await isUp(s.connectorUrl);
  if (up !== wasUp) {
    output.appendLine(up ? `Connected to the local connector at ${s.connectorUrl}.` : `The local connector at ${s.connectorUrl} is not running.`);
    if (up) {
      vscode.window.showInformationMessage(
        "ExpandRG is connected. Start your simulation in the terminal; use Set up simulation for help preparing its commands.");
    } else {
      vscode.window.showWarningMessage("ExpandRG isn't running, so runs can't be watched.", "Start ExpandRG")
        .then((choice) => choice && startConnector(true));
    }
    wasUp = up;
  }
  let watched: Watched[] = [];
  if (up) {
    const [watches, streams] = await Promise.all([
      call<Watched[]>(s.connectorUrl, "/watches"),
      call<Watched[]>(s.connectorUrl, `/streams?member_id=${encodeURIComponent(s.member)}`),
    ]);
    watched = [...watches, ...streams];
  }
  status.text = `$(pulse) ${statusText(up, watched, s.member)}`;
  status.backgroundColor = watched.some((w) => w.member_id === s.member && w.state === "watching" && w.critical > 0)
    ? new vscode.ThemeColor("statusBarItem.errorBackground")
    : undefined;
}

// --- First-time setup ------------------------------------------------------------

// Set up means: ExpandRG can be started here (a Python with it installed, or a checkout of its
// source) and you've said who you are in this workspace.
function isConfigured(): boolean {
  const c = vscode.workspace.getConfiguration("expandrg");
  const isSet = (key: string) => {
    const value = c.inspect<string>(key);
    return Boolean(value?.workspaceValue ?? value?.workspaceFolderValue ?? value?.globalValue);
  };
  return (isSet("pythonPath") || isSet("connectorFolder")) && isSet("member");
}

// How this workspace runs ExpandRG: the installed package, or a checkout when connectorFolder is set.
function engine(): Engine {
  const s = settings();
  return s.connectorFolder ? { python: s.pythonPath, folder: s.connectorFolder } : { python: s.pythonPath };
}

// One guided setup: a Python with ExpandRG (installed from the package this extension carries
// if needed), your group, who you are and this folder's project. Saved in this workspace's
// settings, so nothing has to be typed there.
async function setUp(): Promise<boolean> {
  const save = (key: string, value: unknown) =>
    vscode.workspace.getConfiguration("expandrg").update(key, value, vscode.ConfigurationTarget.Workspace);
  if (!vscode.workspace.workspaceFolders?.length) {
    vscode.window.showWarningMessage("ExpandRG: open your project folder first, then run ExpandRG: Set up.");
    return false;
  }
  const found = findEngine(settings().pythonPath, settings().connectorFolder);
  const wheel = bundledEngine(extensionPath);
  let ready: Engine | undefined = found;
  if (!found) {
    ready = await installEngine(settings().pythonPath, wheel);
  } else if (!found.folder && wheel && found.version !== wheelVersion(wheel)) {
    const update = await vscode.window.showInformationMessage(
      `ExpandRG ${found.version} is installed in ${found.python}; this extension carries ${wheelVersion(wheel)}. Update it?`,
      "Update", "Keep");
    if (update === "Update") ready = await install(found.python, wheel);
  }
  if (!ready) return false;
  await save("pythonPath", ready.python);
  await save("connectorFolder", ready.folder); // none: the installed package is used

  const groupPick = await vscode.window.showQuickPick<vscode.QuickPickItem & { group?: Group }>([
    ...listGroups().map((g) => ({
      label: g.name, description: g.id ? `group ${g.id}` : "for presenting", group: g,
      detail: `${g.people.length} people · projects: ${g.projects.map((p) => p.name).join(", ")}`,
    })),
    { label: "$(add) Set up a new group…", description: "leader only",
      detail: "Asks in a terminal for your group and your CREAO agents' app IDs and keys." },
  ], { title: "ExpandRG setup (1 of 3): which research group are you in?", ignoreFocusOut: true });
  if (!groupPick) return false;
  if (!groupPick.group) {
    const setup = engineCommand(ready, "setup");
    fs.mkdirSync(setup.cwd, { recursive: true });
    vscode.window.createTerminal({ name: "ExpandRG group setup", cwd: setup.cwd, shellPath: setup.python, shellArgs: setup.args }).show();
    vscode.window.showInformationMessage("Answer the questions in the terminal. When it's done, run ExpandRG: Set up again and pick your new group.");
    return false;
  }
  const group = groupPick.group;
  const personPick = await vscode.window.showQuickPick(
    group.people.map((p) => ({ label: p.name, description: p.role, person: p })),
    { title: `ExpandRG setup (2 of 3): who are you in ${group.name}?`, ignoreFocusOut: true },
  );
  if (!personPick) return false;
  const projects = projectsFor(group, personPick.person.id);
  if (!projects.length) {
    vscode.window.showErrorMessage(`ExpandRG: ${personPick.person.name} isn't in a project of ${group.name} yet.`);
    return false;
  }
  const projectPick = projects.length === 1 ? { project: projects[0] } : await vscode.window.showQuickPick(
    projects.map((p) => ({ label: p.name, description: p.id, project: p })),
    { title: "ExpandRG setup (3 of 3): which project is this folder?", ignoreFocusOut: true },
  );
  if (!projectPick) return false;

  await save("group", group.id);
  await save("member", personPick.person.id);
  await save("project", projectPick.project.id);
  // So ExpandRG sees the runs you type in this workspace's terminal.
  await vscode.workspace.getConfiguration("terminal.integrated").update("shellIntegration.enabled", true, vscode.ConfigurationTarget.Workspace);
  output.appendLine(`Set up as ${personPick.person.id} in ${group.id || "the demo"}, project ${projectPick.project.id}, ` +
    `Python ${ready.python}${ready.folder ? ` with the ExpandRG checkout ${ready.folder}` : ""}.`);

  await ensureGroupRunning(group);
  const choice = await vscode.window.showInformationMessage(
    `ExpandRG is set up: ${personPick.person.name} · ${group.name} · ${projectPick.project.name}.`, "Open ExpandRG AI", "Open dashboard");
  if (choice === "Open ExpandRG AI") void vscode.commands.executeCommand("expandrg.openPersonalAgent");
  if (choice === "Open dashboard") openDashboard();
  return true;
}

// The first Python with ExpandRG installed: the configured one, then the ones on PATH. With
// expandrg.connectorFolder set to a checkout of ExpandRG's source and no installed package, the
// first Python with its requirements runs the checkout instead (also looking in or next to it).
function findEngine(configured: string, folder: string): (Engine & { version?: string }) | undefined {
  const candidates = pythonCandidates(configured, folder && isExpandrgFolder(folder) ? folder : "");
  for (const python of candidates) {
    const check = spawnSync(python, ["-c", "import expandrg; print(expandrg.__version__)"], { encoding: "utf8", timeout: 20000, windowsHide: true });
    if (check.status === 0) return { python, version: check.stdout.trim() };
  }
  if (folder && isExpandrgFolder(folder)) {
    const python = candidates.find((p) =>
      spawnSync(p, ["-c", "import fastapi, uvicorn, dotenv, requests"], { timeout: 20000, windowsHide: true }).status === 0);
    if (python) return { python, folder };
  }
  return undefined;
}

function versionOf(python: string): [number, number] | undefined {
  const check = spawnSync(python, ["--version"], { encoding: "utf8", timeout: 20000, windowsHide: true });
  return check.status === 0 ? pythonVersion(`${check.stdout} ${check.stderr}`) : undefined;
}

// ExpandRG isn't installed in any Python yet: install the package this extension carries into
// one the person chooses (the first Python 3.10+ found, or one they pick, e.g. a virtual environment's).
async function installEngine(configured: string, wheel: string | undefined): Promise<Engine | undefined> {
  if (!wheel) {
    vscode.window.showErrorMessage("ExpandRG: this copy of the extension doesn't carry ExpandRG's package. " +
      "Install it with pip (python -m pip install expandrg-<version>.whl), then run ExpandRG: Set up again.");
    return undefined;
  }
  const found = pythonCandidates(configured).find((python) => supportedPython(versionOf(python)));
  const choice = found
    ? await vscode.window.showInformationMessage(
      `ExpandRG isn't installed on this computer yet. Install it into ${found}? It takes about a minute and needs the internet once.`,
      "Install", "Pick another Python…")
    : await vscode.window.showWarningMessage("ExpandRG needs Python 3.10 or newer. Pick the Python to install it into.", "Pick Python…");
  let python = choice === "Install" ? found : undefined;
  if (choice?.startsWith("Pick")) {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true, openLabel: "Install ExpandRG here",
      title: "The Python to install ExpandRG into (3.10 or newer, for example a virtual environment's python.exe)",
    });
    python = picked?.[0].fsPath;
    if (python && !supportedPython(versionOf(python))) {
      vscode.window.showErrorMessage(`ExpandRG: ${python} isn't Python 3.10 or newer.`);
      return undefined;
    }
  }
  return python ? install(python, wheel) : undefined;
}

// pip install the package into that Python, showing pip's output under Output → ExpandRG.
async function install(python: string, wheel: string): Promise<Engine | undefined> {
  output.appendLine(`Installing ${path.basename(wheel)} into ${python}.`);
  const ok = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `ExpandRG: installing into ${python}…` },
    () => new Promise<boolean>((resolve) => {
      const pip = spawn(python, ["-m", "pip", "install", "--upgrade", "--disable-pip-version-check", wheel], { windowsHide: true });
      pip.stdout.on("data", (d) => output.append(String(d)));
      pip.stderr.on("data", (d) => output.append(String(d)));
      pip.on("error", (e) => { output.appendLine(e.message); resolve(false); });
      pip.on("close", (code) => resolve(code === 0));
    }));
  if (!ok) {
    output.show(true);
    vscode.window.showErrorMessage("ExpandRG couldn't be installed. Output → ExpandRG shows pip's reason.");
    return undefined;
  }
  return { python };
}

// ExpandRG serves one group at a time: start the chosen one, or offer to switch to it.
async function ensureGroupRunning(group: Group) {
  const s = settings();
  let running: string | undefined;
  try {
    running = (await call<{ group?: string }>(s.backendUrl, "/health", { timeoutMs: 3000 })).group ?? "demo";
  } catch {
    running = undefined; // not running: start it below
  }
  const wanted = group.id || "demo";
  if (running === wanted) return;
  if (running) {
    const now = running === "demo" ? "the demo group" : `the group "${running}"`;
    const choice = await vscode.window.showWarningMessage(
      `ExpandRG is running ${now}, not ${group.name}. Restart it with ${group.name}?`, "Restart ExpandRG", "Not now");
    if (choice !== "Restart ExpandRG") return;
    output.appendLine(`Stopping ExpandRG for the switch: ${stopExpandrg()}`);
  }
  await startConnector(true);
}

function stopExpandrg(): string {
  const stop = engineCommand(engine(), "stop");
  fs.mkdirSync(stop.cwd, { recursive: true });
  const stopped = spawnSync(stop.python, stop.args, { cwd: stop.cwd, encoding: "utf8", timeout: 60000, windowsHide: true });
  return (stopped.stdout || stopped.stderr || "").trim();
}

// --- Commands ------------------------------------------------------------------

function openDashboard() {
  const s = settings();
  vscode.env.openExternal(vscode.Uri.parse(dashboardUrl(s)));
}

// Sign in to the backend as the configured member and pick one of their projects.
async function chooseProject() {
  const s = settings();
  try {
    const { token } = await call<{ token: string }>(s.backendUrl, "/sign-in", { method: "POST", body: { member_id: s.member } });
    const me = await call<{ projects: { id: string; name: string }[] }>(s.backendUrl, "/me", { token });
    const picked = await vscode.window.showQuickPick(
      me.projects.map((p) => ({ label: p.name, description: p.id, id: p.id })),
      { placeHolder: `Which ExpandRG project is this folder? (signed in as ${s.member})` },
    );
    if (picked) {
      await vscode.workspace.getConfiguration("expandrg").update("project", picked.id, vscode.ConfigurationTarget.Workspace);
      vscode.window.showInformationMessage(`ExpandRG: this folder is now "${picked.label}".`);
    }
  } catch (e) {
    vscode.window.showErrorMessage(`ExpandRG: could not reach the backend at ${s.backendUrl}. ${message(e)}`);
  }
}

// Pick a log file in this workspace and have the local connector watch it.
async function watchLogFile() {
  const s = settings();
  const folder = await pickFolder();
  const root = folder && (await ensureProject()) ? await ensureGranted(folder.uri.fsPath) : undefined;
  if (!folder || !root) return;
  const files = await vscode.workspace.findFiles(
    new vscode.RelativePattern(folder, "**/*.{log,out,err,txt}"),
    "{**/node_modules/**,**/.git/**,**/.venv/**,**/venv/**}",
    200,
  );
  if (!files.length) {
    vscode.window.showInformationMessage("ExpandRG: no log files (.log, .out, .err, .txt) in this folder yet.");
    return;
  }
  const picked = await vscode.window.showQuickPick(
    files.map((f) => ({ label: path.relative(folder.uri.fsPath, f.fsPath).replace(/\\/g, "/") })),
    { placeHolder: "Which log file does your simulation write?" },
  );
  if (!picked) return;
  try {
    await call(s.connectorUrl, "/watches", {
      method: "POST",
      body: { root, file: path.join(folder.uri.fsPath, picked.label), project_id: settings().project, member_id: s.member },
    });
    vscode.window.showInformationMessage(`ExpandRG: watching ${picked.label}. It only reads the file.`, "Open dashboard")
      .then((choice) => choice && openDashboard());
  } catch (e) {
    vscode.window.showErrorMessage(`ExpandRG: could not start watching. ${message(e)}`);
  }
}

async function stopWatching() {
  const s = settings();
  try {
    const watches = (await call<(Watched & { id: string })[]>(s.connectorUrl, "/watches"))
      .filter((w) => w.member_id === s.member && w.state === "watching");
    if (!watches.length) {
      vscode.window.showInformationMessage("ExpandRG: nothing is being watched.");
      return;
    }
    const picked = await vscode.window.showQuickPick(
      watches.map((w) => ({ label: w.file ?? w.id, id: w.id })),
      { placeHolder: "Stop watching which log file? (the simulation keeps running)" },
    );
    if (picked) await call(s.connectorUrl, `/watches/${picked.id}`, { method: "DELETE" });
  } catch (e) {
    vscode.window.showErrorMessage(`ExpandRG: ${message(e)}`);
  }
}

// --- The local connector -------------------------------------------------------

// Start ExpandRG (backend, local connector and dashboard: `expandrg start --no-browser`, or a
// checkout's run_demo.py) unless it is already running. It runs on its own, outside VS Code, so
// reloading or closing VS Code doesn't stop it. What it prints goes to launcher.log in its logs
// folder; if it stops without starting, the reason is shown at once.
async function startConnector(askedByUser: boolean) {
  const s = settings();
  if (await isUp(s.connectorUrl)) {
    if (askedByUser) vscode.window.showInformationMessage("ExpandRG is already running.");
    return;
  }
  if (!isConfigured()) {
    if (askedByUser) {
      vscode.window.showWarningMessage("ExpandRG: run Set up first, so ExpandRG knows which Python runs it.", "Set up")
        .then((choice) => choice && setUp());
    }
    return;
  }
  if (await isUp(s.backendUrl)) {
    // Half running (its connector stopped): stop the rest, so it can start cleanly.
    output.appendLine(`ExpandRG's backend is running without its connector; stopping it first: ${stopExpandrg()}`);
  }
  const start = engineCommand(engine(), "start", ["--no-browser", ...(s.group ? ["--group", s.group] : [])]);
  fs.mkdirSync(start.cwd, { recursive: true });
  fs.mkdirSync(start.logs, { recursive: true });
  const logFile = path.join(start.logs, "launcher.log");
  const log = fs.openSync(logFile, "w");
  output.appendLine(`Starting ExpandRG: ${start.python} ${start.args.join(" ")} (in ${start.cwd}).`);
  let ended = false;
  connectorProcess = spawn(start.python, start.args, {
    cwd: start.cwd, detached: true, stdio: ["ignore", log, log], windowsHide: true, env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  fs.closeSync(log);
  connectorProcess.on("error", (e) => { ended = true; output.appendLine(`Could not start ExpandRG: ${e.message}`); });
  connectorProcess.on("exit", () => { ended = true; });
  connectorProcess.unref();
  const progress = vscode.window.setStatusBarMessage("ExpandRG: starting…");
  for (let i = 0; i < 90 && !ended && !(await isUp(s.connectorUrl)); i++) await new Promise((r) => setTimeout(r, 1000));
  progress.dispose();
  const up = await isUp(s.connectorUrl);
  if (up) {
    output.appendLine("ExpandRG started.");
    if (askedByUser) vscode.window.showInformationMessage("ExpandRG is running.");
    return;
  }
  const said = fs.existsSync(logFile) ? fs.readFileSync(logFile, "utf8").split(/\r?\n/).filter((l) => l.trim()) : [];
  output.appendLine(`ExpandRG didn't start. ${logFile}:`);
  said.slice(-12).forEach((line) => output.appendLine(`  ${line}`));
  vscode.window.showWarningMessage(`ExpandRG didn't start: ${said.at(-1) ?? `see ${logFile}`}`, "Show details")
    .then((choice) => choice && output.show(true));
}

// --- Helpers -------------------------------------------------------------------

async function pickFolder(): Promise<vscode.WorkspaceFolder | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (!folders.length) {
    vscode.window.showWarningMessage("ExpandRG: open your project folder first.");
    return undefined;
  }
  if (folders.length === 1) return folders[0];
  return vscode.window.showWorkspaceFolderPick({ placeHolder: "Which project folder?" });
}

async function ensureProject(): Promise<boolean> {
  if (settings().project) return true;
  await chooseProject();
  return Boolean(settings().project);
}

// ExpandRG only reads folders the researcher allowed. Returns the allowed folder that
// contains this one, asking once if there is none.
async function ensureGranted(folder: string): Promise<string | undefined> {
  const s = settings();
  try {
    const grants = await call<{ root: string }[]>(s.connectorUrl, "/grants");
    const granted = grants.find((g) => insideFolder(folder, g.root));
    if (granted) return granted.root;
    const choice = await vscode.window.showInformationMessage(
      `Allow ExpandRG to read "${path.basename(folder)}"? It only reads; it never sends .env files or keys, and never stops a run.`,
      { modal: true },
      "Allow",
    );
    if (choice !== "Allow") return undefined;
    const added = await call<{ root: string }>(s.connectorUrl, "/grants", { method: "POST", body: { root: folder } });
    return added.root;
  } catch (e) {
    const hint = e instanceof HttpError ? message(e) : "Is the local connector running? (ExpandRG: Start the local connector)";
    vscode.window.showErrorMessage(`ExpandRG: ${hint}`);
    return undefined;
  }
}

// The granted folder the personal agent may read, by name: the one holding an open workspace
// folder. With ask, a single open folder that isn't allowed yet is offered once per session.
let chatFolderDeclined = false;
async function chatFolder(ask: boolean): Promise<string | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (!folders.length || !vscode.workspace.isTrusted) return undefined;
  let grants: { root: string; name: string }[];
  try {
    grants = await call(settings().connectorUrl, "/grants", { timeoutMs: 3000 });
  } catch {
    return undefined; // the connector isn't running: the agent is told it can't read files
  }
  for (const folder of folders) {
    const granted = grants.find((g) => insideFolder(folder.uri.fsPath, g.root));
    if (granted) return granted.name;
  }
  if (!ask || chatFolderDeclined || folders.length !== 1) return undefined;
  const root = await ensureGranted(folders[0].uri.fsPath);
  if (!root) chatFolderDeclined = true;
  return root ? path.basename(root) : undefined;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
