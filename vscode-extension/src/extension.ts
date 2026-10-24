// ExpandRG in VS Code. The extension is the face; the local connector (Python) is the
// engine: it reads the runs, runs the checks and sends numbers and problems to the
// backend, which the web dashboard shows. The extension never starts, pauses or stops
// a simulation.

import { ChildProcess, spawn } from "child_process";
import * as path from "path";
import * as vscode from "vscode";
import { call, HttpError, isUp } from "./http";
import { dashboardUrl, insideFolder, statusText, Watched } from "./logic";
import { watchTerminalRuns } from "./runs";
import { PersonalAgentPanel } from "./personalAgent";

const POLL_MS = 3000;
let connectorProcess: ChildProcess | undefined;
let output: vscode.OutputChannel;

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
  );
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

// Start ExpandRG (backend, local connector and dashboard: run_demo.py --no-browser) from
// expandrg.connectorFolder unless it is already running. It runs on its own, outside VS
// Code, so reloading or closing VS Code doesn't stop it. Its logs go to .demo-logs/.
async function startConnector(askedByUser: boolean) {
  const s = settings();
  if (await isUp(s.connectorUrl)) {
    if (askedByUser) vscode.window.showInformationMessage("ExpandRG is already running.");
    return;
  }
  if (!s.connectorFolder) {
    if (askedByUser) {
      vscode.window.showWarningMessage(
        "ExpandRG: set \"expandrg.connectorFolder\" to your ExpandRG folder (the one with run_demo.py), or run: python run_demo.py",
      );
    }
    return;
  }
  const args = ["run_demo.py", "--no-browser", ...(s.group ? ["--group", s.group] : [])];
  output.appendLine(`Starting ExpandRG: ${s.pythonPath} ${args.join(" ")} (in ${s.connectorFolder}).`);
  connectorProcess = spawn(s.pythonPath, args, {
    cwd: s.connectorFolder, detached: true, stdio: "ignore", windowsHide: true,
  });
  connectorProcess.on("error", (e) => output.appendLine(`Could not start ExpandRG: ${e.message}`));
  connectorProcess.unref();
  const progress = vscode.window.setStatusBarMessage("ExpandRG: starting…");
  for (let i = 0; i < 90 && !(await isUp(s.connectorUrl)); i++) await new Promise((r) => setTimeout(r, 1000));
  progress.dispose();
  const up = await isUp(s.connectorUrl);
  output.appendLine(up ? "ExpandRG started." : "ExpandRG didn't start within 90 s. See .demo-logs/ in the ExpandRG folder.");
  if (askedByUser || !up) {
    vscode.window.showInformationMessage(up ? "ExpandRG is running." : "ExpandRG didn't start. See .demo-logs/ in the ExpandRG folder.");
  }
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
