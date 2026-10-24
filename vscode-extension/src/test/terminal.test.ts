import { strict as assert } from "node:assert";
import { test } from "node:test";
import { insideFolder, LineSplitter, looksLikeRun, matchesEntryPoint, redirectTarget, runPart, stripAnsi, words } from "../logic";

test("a run's folder counts as allowed when it is inside an allowed folder", () => {
  assert.ok(insideFolder("c:\\research\\heat\\cases\\fine", "C:\\research\\heat", true));
  assert.ok(insideFolder("C:/research/heat", "c:\\research\\heat\\", true));
  assert.ok(!insideFolder("C:\\research\\heat2", "C:\\research\\heat", true));
});

const heat = [["{python}", "heat2d.py", "config.json"]];

test("a command running an expandrg.json entry point is recognised", () => {
  assert.ok(matchesEntryPoint("python heat2d.py config.json", heat));
  assert.ok(matchesEntryPoint("python3 -u .\\heat2d.py other.json --delay 2", heat));
  assert.ok(matchesEntryPoint("C:\\Python312\\python.exe heat2d.py config.json > live.log", heat));
  assert.ok(matchesEntryPoint("py ./heat2d.py", heat));
  assert.ok(!matchesEntryPoint("python plot_results.py", heat));
  assert.ok(!matchesEntryPoint("node heat2d.py", heat));
  assert.ok(matchesEntryPoint("./solver --steps 10", [["./solver"]]));
});

test("only commands that look like runs are asked about", () => {
  for (const run of ["python train.py", "py -3 sim.py", "mpirun -n 4 ./solver", ".\\solver.exe", "./run.sh", "julia sim.jl", "Rscript fit.R"]) {
    assert.ok(looksLikeRun(run), run);
  }
  for (const other of ["git status", "cd tests", "ls", "npm install", "pip install numpy", "code ."]) {
    assert.ok(!looksLikeRun(other), other);
  }
});

test("output sent to a file is found, in bash and PowerShell forms", () => {
  assert.equal(redirectTarget("python heat2d.py config.json > live.log"), "live.log");
  assert.equal(redirectTarget("python heat2d.py >> logs/run.out 2>&1"), "logs/run.out");
  assert.equal(redirectTarget('python heat2d.py *> "my run.log"'), "my run.log");
  assert.equal(redirectTarget("python heat2d.py | tee live.log"), "live.log");
  assert.equal(redirectTarget("python heat2d.py | Tee-Object -FilePath live.log"), "live.log");
  assert.equal(redirectTarget("python heat2d.py 2> errors.txt"), undefined);
  assert.equal(redirectTarget("python heat2d.py config.json"), undefined);
});

test("terminal output becomes clean lines, even when split mid-line", () => {
  const splitter = new LineSplitter();
  const first = splitter.push("\x1b]633;C\x07\x1b[32mstep   100  max|T| = 2.7685e+02\x1b[0m\r\nstep   2");
  const second = splitter.push("00  max|T| = 2.7549e+14\r\n\r\n");
  const last = splitter.push("done");
  assert.deepEqual(first, ["step   100  max|T| = 2.7685e+02"]);
  assert.deepEqual(second, ["step   200  max|T| = 2.7549e+14"]);
  assert.deepEqual(last, []);
  assert.deepEqual(splitter.flush(), ["done"]);
  assert.equal(stripAnsi("\x1b[1;31mERROR\x1b[0m"), "ERROR");
});

test("cd before the run on the same line is followed", () => {
  const join = (from: string, to: string) => (to.includes(":") ? to : `${from}\\${to}`);
  assert.deepEqual(runPart("cd C:\\sim\\heat; python heat2d.py config.json", "C:\\Users\\me", join),
    { command: "python heat2d.py config.json", cwd: "C:\\sim\\heat" });
  assert.deepEqual(runPart("cd cases && ./solver > out.log", "/home/me", (a, b) => `${a}/${b}`),
    { command: "./solver > out.log", cwd: "/home/me/cases" });
  assert.deepEqual(runPart("python heat2d.py", "C:\\sim", join), { command: "python heat2d.py", cwd: "C:\\sim" });
});

test("quoted words stay together", () => {
  assert.deepEqual(words('python "my sim.py" --name \'a b\''), ["python", "my sim.py", "--name", "a b"]);
});
