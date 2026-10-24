// Plain logic with no VS Code imports, so it can be tested with node --test.

export interface Settings {
  member: string;
  project: string;
  dashboardUrl: string;
}

export interface Watched {
  member_id: string;
  file?: string; // a watched log file
  label?: string; // a streamed terminal run
  state: string;
  critical: number;
}

// The dashboard's Runs page for this member and project.
export function dashboardUrl(settings: Settings, view = "alerts"): string {
  const params = new URLSearchParams({ as: settings.member, view });
  if (settings.project) params.set("project", settings.project);
  return `${settings.dashboardUrl.replace(/\/+$/, "")}/?${params}`;
}

// The status bar text: what is being watched right now, and its Critical problems.
export function statusText(connectorUp: boolean, watched: Watched[], member: string): string {
  if (!connectorUp) return "ExpandRG: connector off";
  const mine = watched.filter((w) => w.member_id === member && w.state === "watching");
  if (!mine.length) return "ExpandRG: not watching";
  const critical = mine.reduce((sum, w) => sum + w.critical, 0);
  const name = mine.length === 1 ? (mine[0].file ?? mine[0].label ?? "a run") : `${mine.length} runs`;
  return `ExpandRG: watching ${name}${critical ? ` · ${critical} critical` : ""}`;
}

// Whether two folder paths are the same folder (Windows paths ignore case and slash direction).
export function sameFolder(a: string, b: string, windows = process.platform === "win32"): boolean {
  const clean = (p: string) => {
    const s = p.replace(/\\/g, "/").replace(/\/+$/, "");
    return windows ? s.toLowerCase() : s;
  };
  return clean(a) === clean(b);
}

// Whether child is parent or a folder inside it.
export function insideFolder(child: string, parent: string, windows = process.platform === "win32"): boolean {
  const clean = (p: string) => {
    const s = p.replace(/\\/g, "/").replace(/\/+$/, "");
    return windows ? s.toLowerCase() : s;
  };
  return clean(child) === clean(parent) || clean(child).startsWith(clean(parent) + "/");
}

// --- Runs started in the terminal ---------------------------------------------------

const PYTHON = /^(python[0-9.]*|py)(\.exe)?$/i;
const RUNNERS = /^(python[0-9.]*|py|mpirun|mpiexec|julia|rscript)(\.exe)?$/i;

// Split a command line into words, keeping "quoted words" together.
export function words(commandLine: string): string[] {
  return [...commandLine.matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)].map((m) => m[1] ?? m[2] ?? m[3]);
}

function baseName(word: string): string {
  return word.replace(/\\/g, "/").split("/").pop() ?? word;
}

// Whether the typed command runs one of the project's entry points (expandrg.json):
// the same program ("{python}" is any Python) and the same script.
export function matchesEntryPoint(commandLine: string, entryCommands: string[][]): boolean {
  const typed = words(commandLine);
  if (!typed.length) return false;
  return entryCommands.some((command) => {
    const [program, ...args] = command;
    const programOk = program === "{python}" ? PYTHON.test(baseName(typed[0]))
                                             : baseName(typed[0]).toLowerCase() === baseName(program).toLowerCase();
    const script = args.find((a) => !a.startsWith("-"));
    const typedScript = typed.slice(1).find((w) => !w.startsWith("-"));
    return programOk && (!script || (typedScript !== undefined && baseName(typedScript).toLowerCase() === baseName(script).toLowerCase()));
  });
}

// The part of a command line that runs something, and the folder it runs in:
// "cd C:\sim; python heat2d.py" runs "python heat2d.py" in C:\sim.
export function runPart(commandLine: string, cwd: string, resolve: (from: string, to: string) => string): { command: string; cwd: string } {
  let dir = cwd;
  for (const part of commandLine.split(/;|&&/).map((p) => p.trim()).filter(Boolean)) {
    const [first, target] = words(part);
    if (/^(cd|chdir|set-location|sl|pushd)$/i.test(first) && target) {
      dir = resolve(dir, target);
      continue;
    }
    return { command: part, cwd: dir };
  }
  return { command: commandLine.trim(), cwd: dir };
}

// Whether a command looks like a simulation run (worth asking about), not git, cd, ls, ...
export function looksLikeRun(commandLine: string): boolean {
  const first = words(commandLine)[0] ?? "";
  return RUNNERS.test(baseName(first)) || /^\.{1,2}[\\/]/.test(first);
}

// The file a command's output is sent to ("> live.log", ">> out.txt", "| tee run.log"), if any.
export function redirectTarget(commandLine: string): string | undefined {
  const quoted = String.raw`("[^"]+"|'[^']+'|[^\s;|&]+)`;
  const redirect = commandLine.match(new RegExp(String.raw`(?:^|\s)[*1]?>{1,2}\s*` + quoted));
  const tee = commandLine.match(new RegExp(String.raw`\|\s*(?:tee|tee-object)\s+(?:-a\s+|--append\s+|-filepath\s+|-append\s+)*` + quoted, "i"));
  const target = (redirect ?? tee)?.[1];
  return target?.replace(/^["']|["']$/g, "");
}

// Terminal output arrives in chunks with colour codes; this turns it into clean lines.
export function stripAnsi(text: string): string {
  return text
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, "") // window titles and shell markers
    .replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "") // colours and cursor moves
    .replace(/\x1b[@-_]/g, "");
}

export class LineSplitter {
  private rest = "";

  push(chunk: string): string[] {
    const lines = (this.rest + stripAnsi(chunk)).split(/\r?\n/);
    this.rest = lines.pop() ?? "";
    return lines.map((l) => l.replace(/\r/g, "")).filter((l) => l.trim());
  }

  flush(): string[] {
    const last = this.rest.replace(/\r/g, "").trim();
    this.rest = "";
    return last ? [last] : [];
  }
}
