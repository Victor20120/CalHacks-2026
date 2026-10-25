import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import {
  bundledEngine, DEMO_GROUP, engineCommand, expandrgHome, isExpandrgFolder, listGroups, projectsFor, pythonCandidates,
  pythonVersion, supportedPython, wheelVersion,
} from "../setup";

function tempDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "expandrg-setup-"));
}

test("groups set up on this computer are listed by name with their people and projects, then the demo", () => {
  const home = tempDir();
  const folder = path.join(home, "groups", "fluids-lab");
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, "group.json"), JSON.stringify({
    id: "fluids-lab", name: "Fluids Lab",
    leader: { id: "maya", name: "Maya" },
    researchers: [{ id: "ana", name: "Ana", agent_slot: "AGENT_1" }, { id: "ben", name: "Ben", agent_slot: "AGENT_2" }],
    projects: [{ id: "channel", name: "Channel flow", members: ["maya", "ana"] }],
  }));
  fs.mkdirSync(path.join(home, "groups", "half-written"));
  fs.writeFileSync(path.join(home, "groups", "half-written", "group.json"), "{ not json");

  const groups = listGroups(home);
  assert.deepEqual(groups.map((g) => g.name), ["Fluids Lab", "Demo group"]);
  assert.deepEqual(groups[0].people.map((p) => [p.id, p.role]), [["maya", "leader"], ["ana", "researcher"], ["ben", "researcher"]]);
  assert.deepEqual(projectsFor(groups[0], "ana").map((p) => p.id), ["channel"]);
  assert.deepEqual(projectsFor(groups[0], "ben"), []); // not on a project yet
  assert.equal(groups[1], DEMO_GROUP);
  assert.deepEqual(projectsFor(DEMO_GROUP, "researcher_b").map((p) => p.id), ["heat-diffusion"]);
  assert.deepEqual(listGroups(tempDir()), [DEMO_GROUP]); // nothing set up yet
});

test("ExpandRG's home follows EXPANDRG_HOME, else ~/.expandrg", () => {
  assert.equal(expandrgHome({ EXPANDRG_HOME: "/data/expandrg" }), "/data/expandrg");
  assert.equal(expandrgHome({}), path.join(os.homedir(), ".expandrg"));
});

test("the ExpandRG folder is recognised by run_demo.py and backend/", () => {
  const folder = tempDir();
  assert.equal(isExpandrgFolder(folder), false);
  fs.writeFileSync(path.join(folder, "run_demo.py"), "");
  fs.mkdirSync(path.join(folder, "backend"));
  assert.equal(isExpandrgFolder(folder), true);
});

test("Python candidates: the configured one, virtual environments in or next to ExpandRG, then PATH", () => {
  const parent = tempDir();
  const repo = path.join(parent, "ExpandRG");
  fs.mkdirSync(repo);
  for (const venv of [path.join(parent, "calhack"), path.join(repo, ".venv")]) {
    fs.mkdirSync(path.join(venv, "Scripts"), { recursive: true });
    fs.writeFileSync(path.join(venv, "pyvenv.cfg"), "");
    fs.writeFileSync(path.join(venv, "Scripts", "python.exe"), "");
  }
  fs.mkdirSync(path.join(parent, "not-a-venv", "Scripts"), { recursive: true });
  fs.writeFileSync(path.join(parent, "not-a-venv", "Scripts", "python.exe"), "");

  const found = pythonCandidates("C:\\custom\\python.exe", repo, "win32");
  assert.deepEqual(found, ["C:\\custom\\python.exe", path.join(repo, ".venv", "Scripts", "python.exe"),
    path.join(parent, "calhack", "Scripts", "python.exe"), "python", "py"]);
  assert.deepEqual(pythonCandidates("python", path.join(parent, "missing"), "linux"), ["python", "python3"]);
  assert.deepEqual(pythonCandidates("C:\\custom\\python.exe", "", "win32"), ["C:\\custom\\python.exe", "python", "py"]); // no checkout
});

test("Python 3.10 or newer is needed, read from python --version", () => {
  assert.deepEqual(pythonVersion("Python 3.12.7\n"), [3, 12]);
  assert.equal(pythonVersion("'python' is not recognized"), undefined);
  assert.equal(supportedPython([3, 12]), true);
  assert.equal(supportedPython([3, 10]), true);
  assert.equal(supportedPython([3, 9]), false);
  assert.equal(supportedPython(undefined), false);
});

test("the extension finds the ExpandRG package it carries, newest version first", () => {
  const extension = tempDir();
  assert.equal(bundledEngine(extension), undefined); // a copy built without it
  fs.mkdirSync(path.join(extension, "engine"));
  for (const name of ["expandrg-0.12.9-py3-none-any.whl", "expandrg-0.13.0-py3-none-any.whl", "notes.txt"]) {
    fs.writeFileSync(path.join(extension, "engine", name), "");
  }
  const wheel = bundledEngine(extension);
  assert.equal(wheel, path.join(extension, "engine", "expandrg-0.13.0-py3-none-any.whl"));
  assert.equal(wheelVersion(wheel!), "0.13.0");
});

test("ExpandRG runs as the installed package from its own folder, or from a checkout's scripts", () => {
  const home = path.join("/home/ana", ".expandrg");
  assert.deepEqual(engineCommand({ python: "py" }, "start", ["--no-browser", "--group", "fluids-lab"], home), {
    python: "py", args: ["-m", "expandrg", "start", "--no-browser", "--group", "fluids-lab"], cwd: home, logs: path.join(home, "logs"),
  });
  assert.deepEqual(engineCommand({ python: "py" }, "setup", [], home).args, ["-m", "expandrg", "setup"]);
  const checkout = { python: "C:\\venv\\python.exe", folder: "C:\\src\\ExpandRG" };
  assert.deepEqual(engineCommand(checkout, "stop", [], home), {
    python: checkout.python, args: ["run_demo.py", "--stop"], cwd: checkout.folder, logs: path.join(checkout.folder, ".demo-logs"),
  });
  assert.deepEqual(engineCommand(checkout, "setup", [], home).args, ["setup_expandrg.py"]);
});
