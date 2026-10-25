// First-time setup, without editing settings files: a Python with ExpandRG installed (the
// extension carries the package and installs it), which group you're in, who you are and which
// project this folder is. Groups made with `expandrg setup` are read from
// ~/.expandrg/groups/<id>/group.json (names and projects only, never keys).
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

export interface Person { id: string; name: string; role: "leader" | "researcher"; }
export interface Project { id: string; name: string; members: string[]; }
export interface Group { id: string; name: string; people: Person[]; projects: Project[]; }

// The demo group, as backend/workspaces/demo.py makes it. Its ID is empty: started without --group.
export const DEMO_GROUP: Group = {
  id: "",
  name: "Demo group",
  people: [
    { id: "leader", name: "Group Leader", role: "leader" },
    { id: "researcher_a", name: "Researcher A", role: "researcher" },
    { id: "researcher_b", name: "Researcher B", role: "researcher" },
    { id: "researcher_c", name: "Researcher C", role: "researcher" },
  ],
  projects: [
    { id: "heat-diffusion", name: "Heat diffusion solver", members: ["leader", "researcher_a", "researcher_b", "researcher_c"] },
    { id: "wave-propagation", name: "Wave propagation solver", members: ["leader", "researcher_a"] },
  ],
};

export function expandrgHome(env: NodeJS.ProcessEnv = process.env): string {
  return env.EXPANDRG_HOME || path.join(os.homedir(), ".expandrg");
}

// Groups set up on this computer, then the demo. A group file that can't be read is skipped.
export function listGroups(home: string = expandrgHome()): Group[] {
  const groups: Group[] = [];
  const folder = path.join(home, "groups");
  for (const entry of fs.existsSync(folder) ? fs.readdirSync(folder).sort() : []) {
    try {
      const raw = JSON.parse(fs.readFileSync(path.join(folder, entry, "group.json"), "utf8"));
      groups.push({
        id: raw.id,
        name: raw.name,
        people: [{ id: raw.leader.id, name: raw.leader.name, role: "leader" },
          ...raw.researchers.map((r: { id: string; name: string }) => ({ id: r.id, name: r.name, role: "researcher" as const }))],
        projects: raw.projects.map((p: Project) => ({ id: p.id, name: p.name, members: p.members })),
      });
    } catch {
      // not a group folder (or a half-written one): leave it out
    }
  }
  return [...groups, DEMO_GROUP];
}

export function projectsFor(group: Group, memberId: string): Project[] {
  return group.projects.filter((p) => p.members.includes(memberId));
}

export function isExpandrgFolder(folder: string): boolean {
  return fs.existsSync(path.join(folder, "run_demo.py")) && fs.existsSync(path.join(folder, "backend"));
}

// Pythons to try for running ExpandRG, most likely first: the configured one, a virtual
// environment inside or next to an ExpandRG checkout (when there is one), then the ones on PATH.
export function pythonCandidates(configured: string, expandrgFolder = "", platform = process.platform): string[] {
  const exe = platform === "win32" ? path.join("Scripts", "python.exe") : path.join("bin", "python");
  const nearby: string[] = [];
  for (const base of expandrgFolder ? [expandrgFolder, path.dirname(expandrgFolder)] : []) {
    for (const name of fs.existsSync(base) ? fs.readdirSync(base) : []) {
      const candidate = path.join(base, name, exe);
      if (fs.existsSync(path.join(base, name, "pyvenv.cfg")) && fs.existsSync(candidate)) nearby.push(candidate);
    }
  }
  const onPath = platform === "win32" ? ["python", "py"] : ["python3", "python"];
  return [...new Set([configured, ...nearby, ...onPath].filter(Boolean))];
}

// "Python 3.12.7" -> [3, 12]. ExpandRG needs 3.10 or newer.
export function pythonVersion(text: string): [number, number] | undefined {
  const match = /Python (\d+)\.(\d+)/.exec(text);
  return match ? [Number(match[1]), Number(match[2])] : undefined;
}

export function supportedPython(version: [number, number] | undefined): boolean {
  return Boolean(version && (version[0] > 3 || (version[0] === 3 && version[1] >= 10)));
}

// The ExpandRG package the extension carries (engine/expandrg-<version>-*.whl, put there by
// build_package.py), so Set up can install it on a computer that has never had it.
export function bundledEngine(extensionPath: string): string | undefined {
  const folder = path.join(extensionPath, "engine");
  const wheels = fs.existsSync(folder) ? fs.readdirSync(folder).filter((f) => /^expandrg-.+\.whl$/.test(f)).sort() : [];
  return wheels.length ? path.join(folder, wheels[wheels.length - 1]) : undefined;
}

// "expandrg-0.13.0-py3-none-any.whl" -> "0.13.0"
export function wheelVersion(wheel: string): string {
  return path.basename(wheel).split("-")[1] ?? "";
}

// How ExpandRG runs: the installed package (python -m expandrg), or a checkout of its source
// (its run_demo.py and setup_expandrg.py) when expandrg.connectorFolder is set.
export interface Engine { python: string; folder?: string; }

export function engineCommand(engine: Engine, command: "start" | "stop" | "setup", args: string[] = [],
  home: string = expandrgHome()): { python: string; args: string[]; cwd: string; logs: string } {
  if (engine.folder) {
    const script = { start: ["run_demo.py"], stop: ["run_demo.py", "--stop"], setup: ["setup_expandrg.py"] }[command];
    return { python: engine.python, args: [...script, ...args], cwd: engine.folder, logs: path.join(engine.folder, ".demo-logs") };
  }
  // Run from ExpandRG's own folder, so a project folder's modules can't stand in for ExpandRG's.
  return { python: engine.python, args: ["-m", "expandrg", command, ...args], cwd: home, logs: path.join(home, "logs") };
}
