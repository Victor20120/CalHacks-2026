// Runs started in the VS Code terminal. A command in the project's expandrg.json is
// watched automatically; another command that looks like a run is watched after one
// click; anything else is ignored and its output never read. Output going to a file
// ("> live.log") is watched through that file; otherwise the terminal's output is sent,
// line by line, to the local connector. Watching only reads: it never stops a run.

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { call } from "./http";
import { LineSplitter, looksLikeRun, matchesEntryPoint, redirectTarget, runPart } from "./logic";

const SEND_EVERY_MS = 500;
const MAX_BATCH = 2000;
const MAX_WAITING_LINES = 50_000;
const LOG_SETTLE_MS = 2500; // a watched log file is read about once a second

export interface Deps {
  settings(): { member: string; project: string; connectorUrl: string; watchTerminalRuns: boolean };
  ensureProject(): Promise<boolean>;
  ensureGranted(folder: string): Promise<string | undefined>; // the granted root containing folder
  openDashboard(): void;
  log(line: string): void;
}

interface Alert {
  severity: string;
  message: string;
}

interface Run {
  key: object; // the terminal execution, or a token for a run the extension started itself
  execution?: vscode.TerminalShellExecution;
  command: string;
  cwd: string;
  projectRoot: string;
  target?: string; // the file the output goes to, if any
  decision: "pending" | "watch" | "no";
  lines: string[]; // read but not sent yet
  reading?: Promise<void>;
  streamId?: string;
  watchId?: string;
  timer?: NodeJS.Timeout;
  sending: boolean;
  ended: boolean;
  finished: boolean;
  exitCode?: number;
  shown: Set<string>; // severities already shown as a pop-up
  critical: number;
  warnings: number;
}

export interface TrackedProcess {
  write(chunk: string): void;
  end(exitCode: number | undefined): Promise<void>;
}

export function watchTerminalRuns(context: vscode.ExtensionContext, deps: Deps): { trackProcess(command: string, cwd: string): TrackedProcess } {
  const runs = new Map<object, Run>();
  const report = (what: string) => (e: unknown) => deps.log(`${what}: ${e instanceof Error ? e.message : e}`);

  context.subscriptions.push(
    vscode.window.onDidStartTerminalShellExecution((e) => onStart(e.execution).catch(report("Run start"))),
    vscode.window.onDidEndTerminalShellExecution((e) => onEnd(e.execution, e.exitCode).catch(report("Run end"))),
  );

  async function onStart(execution: vscode.TerminalShellExecution) {
    const typed = execution.commandLine.value.trim();
    const terminalCwd = execution.cwd?.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!typed) return;
    if (!deps.settings().watchTerminalRuns) {
      deps.log(`Terminal: "${typed}": not watched (expandrg.watchTerminalRuns is off).`);
      return;
    }
    if (!terminalCwd) {
      deps.log(`Terminal: "${typed}": not watched (VS Code didn't say which folder it runs in).`);
      return;
    }
    const { command, cwd } = runPart(typed, terminalCwd, (from, to) => path.resolve(from, to));
    const manifest = findManifest(cwd);
    const automatic = manifest !== undefined && matchesEntryPoint(command, manifest.commands);
    if (!automatic && !looksLikeRun(command)) {
      deps.log(`Terminal: "${typed}": ignored (not a run).`); // git, cd, ...: never read
      return;
    }
    deps.log(`Terminal: "${command}" in ${cwd}: ${automatic ? "watching (in expandrg.json)" : "asking whether to watch"}.`);

    const run: Run = {
      key: execution, execution, command, cwd, target: redirectTarget(command),
      projectRoot: manifest?.root ?? vscode.workspace.getWorkspaceFolder(vscode.Uri.file(cwd))?.uri.fsPath ?? cwd,
      decision: automatic ? "watch" : "pending", lines: [], sending: false, ended: false, finished: false,
      shown: new Set(), critical: 0, warnings: 0,
    };
    runs.set(execution, run);
    if (!run.target) run.reading = read(run); // must start at once, or the first lines are lost
    if (automatic) await begin(run);
    else ask(run).catch(report("Asking"));
  }

  async function read(run: Run) {
    const splitter = new LineSplitter();
    for await (const chunk of run.execution!.read()) {
      if (run.decision === "no") continue;
      run.lines.push(...splitter.push(chunk));
      if (run.lines.length > MAX_WAITING_LINES) run.lines.splice(0, run.lines.length - MAX_WAITING_LINES);
    }
    if (run.decision !== "no") run.lines.push(...splitter.flush());
  }

  async function ask(run: Run) {
    const choice = await vscode.window.showInformationMessage(
      `ExpandRG: watch this run? ${short(run.command)}`, "Watch", "Not now");
    if (choice !== "Watch") {
      run.decision = "no";
      run.lines = [];
      runs.delete(run.key);
      return;
    }
    run.decision = "watch";
    await begin(run);
    if (run.ended) await finish(run); // it ended while the question was open
  }

  async function begin(run: Run) {
    const root = (await deps.ensureProject()) ? await deps.ensureGranted(run.projectRoot) : undefined;
    if (!root) {
      run.decision = "no";
      deps.log(`Not watching "${run.command}": no project chosen, or the folder wasn't allowed (or the connector is off).`);
      return;
    }
    const s = deps.settings();
    try {
      if (run.target) {
        const file = path.resolve(run.cwd, run.target);
        const watch = await call<{ id: string }>(s.connectorUrl, "/watches", {
          method: "POST", body: { root, file, project_id: s.project, member_id: s.member } });
        run.watchId = watch.id;
      } else {
        const stream = await call<{ id: string }>(s.connectorUrl, "/streams", {
          method: "POST", body: { root, project_id: s.project, member_id: s.member, command: run.command } });
        run.streamId = stream.id;
      }
    } catch (e) {
      run.decision = "no";
      deps.log(`Could not watch "${run.command}": ${e instanceof Error ? e.message : e}`);
      vscode.window.showErrorMessage(`ExpandRG: could not watch this run. ${e instanceof Error ? e.message : e}`);
      return;
    }
    deps.log(`Watching "${run.command}" (${run.target ? `through ${run.target}` : "terminal output"}).`);
    run.timer = setInterval(() => send(run).catch(report("Sending")), SEND_EVERY_MS);
    vscode.window.showInformationMessage(
      `ExpandRG is watching ${run.target ?? "this run"}: ${short(run.command)}. It only reads; it never stops a run.`,
      "Open dashboard").then((c) => c && deps.openDashboard());
  }

  // Send what was read; show the first Warning and the first Critical of the run as pop-ups.
  async function send(run: Run) {
    if (run.sending) return;
    run.sending = true;
    const s = deps.settings();
    try {
      if (run.streamId) {
        while (run.lines.length) {
          const batch = run.lines.splice(0, MAX_BATCH);
          try {
            const result = await call<{ new_alerts: Alert[] }>(s.connectorUrl, `/streams/${run.streamId}/lines`, {
              method: "POST", body: { lines: batch } });
            for (const alert of result.new_alerts) notify(run, alert.severity, alert.message);
          } catch (e) {
            run.lines.unshift(...batch); // try again next time
            throw e;
          }
        }
      } else if (run.watchId) {
        const watches = await call<{ id: string; alerts: number; critical: number }[]>(s.connectorUrl, "/watches");
        const watch = watches.find((w) => w.id === run.watchId);
        if (watch && watch.critical > 0) notify(run, "critical", `Critical problems in ${run.target}.`, watch.critical);
        else if (watch && watch.alerts > 0) notify(run, "warning", `Something looks off in ${run.target}.`, 0, watch.alerts);
      }
    } finally {
      run.sending = false;
    }
  }

  function notify(run: Run, severity: string, message: string, critical?: number, warnings?: number) {
    if (critical !== undefined) run.critical = critical;
    else if (severity === "critical") run.critical++;
    if (warnings !== undefined) run.warnings = warnings;
    else if (severity === "warning") run.warnings++;
    if ((severity !== "critical" && severity !== "warning") || run.shown.has(severity)) return;
    run.shown.add(severity);
    const show = severity === "critical" ? vscode.window.showErrorMessage : vscode.window.showWarningMessage;
    show(`ExpandRG ${severity === "critical" ? "Critical" : "Warning"}: ${message}`, "Open dashboard")
      .then((c) => c && deps.openDashboard());
  }

  async function onEnd(execution: vscode.TerminalShellExecution, exitCode: number | undefined) {
    const run = runs.get(execution);
    if (!run) return;
    run.ended = true;
    run.exitCode = exitCode;
    if (run.decision === "watch" && (run.streamId || run.watchId)) await finish(run);
    else if (run.decision === "no") runs.delete(execution);
  }

  async function finish(run: Run) {
    if (run.finished) return;
    run.finished = true;
    runs.delete(run.key);
    await run.reading;
    if (run.watchId) await new Promise((r) => setTimeout(r, LOG_SETTLE_MS));
    clearInterval(run.timer);
    while (run.sending) await new Promise((r) => setTimeout(r, 50)); // a batch still on its way
    await send(run); // the last lines
    const s = deps.settings();
    if (run.streamId) {
      await call(s.connectorUrl, `/streams/${run.streamId}/end`, { method: "POST", body: { exit_code: run.exitCode ?? null } });
    } else if (run.watchId) {
      await call(s.connectorUrl, `/watches/${run.watchId}`, { method: "DELETE" });
    }
    const failed = run.exitCode !== undefined && run.exitCode !== 0;
    deps.log(`Finished "${run.command}": exit code ${run.exitCode ?? "unknown"}, ${run.critical} critical, ${run.warnings} warnings.`);
    if (run.critical > 0 || failed) {
      const what = failed ? `ended with exit code ${run.exitCode}` : `ended with ${run.critical} Critical problem${run.critical === 1 ? "" : "s"}`;
      vscode.window.showErrorMessage(`ExpandRG: the run ${what}.`, "Open dashboard").then((c) => c && deps.openDashboard());
    } else {
      const found = run.warnings ? `${run.warnings} warning${run.warnings === 1 ? "" : "s"}.` : "No problems found.";
      vscode.window.showInformationMessage(`ExpandRG: the run finished. ${found}`, "Open dashboard").then((c) => c && deps.openDashboard());
    }
  }

  // A run the researcher started with "Run a simulation": its output comes straight from
  // the process, so this works whether or not the terminal reports its commands.
  function trackProcess(command: string, cwd: string): TrackedProcess {
    const key = {};
    const manifest = findManifest(cwd);
    const run: Run = {
      key, command, cwd, projectRoot: manifest?.root ?? cwd, decision: "watch", lines: [], sending: false,
      ended: false, finished: false, shown: new Set(), critical: 0, warnings: 0,
    };
    runs.set(key, run);
    deps.log(`Run a simulation: "${command}" in ${cwd}.`);
    const splitter = new LineSplitter();
    const started = begin(run).catch(report("Run start"));
    return {
      write(chunk: string) {
        run.lines.push(...splitter.push(chunk));
        if (run.lines.length > MAX_WAITING_LINES) run.lines.splice(0, run.lines.length - MAX_WAITING_LINES);
      },
      async end(exitCode: number | undefined) {
        if (run.ended) return;
        run.lines.push(...splitter.flush());
        run.ended = true;
        run.exitCode = exitCode;
        await started;
        if (run.decision === "watch" && run.streamId) await finish(run).catch(report("Run end"));
        else runs.delete(key);
      },
    };
  }

  return { trackProcess };
}

// The nearest expandrg.json at or above the run's folder, and its entry point commands.
function findManifest(start: string): { root: string; commands: string[][] } | undefined {
  let dir = start;
  for (let i = 0; i < 12; i++) {
    const file = path.join(dir, "expandrg.json");
    if (fs.existsSync(file)) {
      try {
        const data = JSON.parse(fs.readFileSync(file, "utf8"));
        const commands = (data.entry_points ?? []).map((e: { command?: string[] }) => e.command ?? []).filter((c: string[]) => c.length);
        return { root: dir, commands };
      } catch {
        return undefined; // the connector explains a broken manifest elsewhere
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return undefined;
}

function short(command: string): string {
  return command.length > 80 ? command.slice(0, 77) + "..." : command;
}
