import { strict as assert } from "node:assert";
import { test } from "node:test";
import { dashboardUrl, sameFolder, statusText } from "../logic";

const settings = { member: "researcher_a", project: "heat-diffusion", dashboardUrl: "http://127.0.0.1:5173/" };

test("the dashboard link opens the Runs page for this member and project", () => {
  assert.equal(dashboardUrl(settings), "http://127.0.0.1:5173/?as=researcher_a&view=alerts&project=heat-diffusion");
  assert.equal(dashboardUrl({ ...settings, project: "" }, "superbrain"), "http://127.0.0.1:5173/?as=researcher_a&view=superbrain");
});

test("the status bar says what is watched and its critical problems", () => {
  const live = { member_id: "researcher_a", file: "live.log", state: "watching", critical: 2 };
  assert.equal(statusText(false, [live], "researcher_a"), "ExpandRG: connector off");
  assert.equal(statusText(true, [], "researcher_a"), "ExpandRG: not watching");
  assert.equal(statusText(true, [live], "researcher_a"), "ExpandRG: watching live.log · 2 critical");
  assert.equal(statusText(true, [{ ...live, critical: 0, file: undefined, label: "python heat2d.py" }], "researcher_a"),
    "ExpandRG: watching python heat2d.py");
  assert.equal(statusText(true, [live, { ...live, critical: 1 }], "researcher_a"), "ExpandRG: watching 2 runs · 3 critical");
});

test("someone else's runs and finished runs are not shown", () => {
  const others = { member_id: "researcher_b", file: "b.log", state: "watching", critical: 5 };
  const finished = { member_id: "researcher_a", file: "old.log", state: "stopped", critical: 1 };
  assert.equal(statusText(true, [others, finished], "researcher_a"), "ExpandRG: not watching");
});

test("folders compare like the operating system does", () => {
  assert.ok(sameFolder("C:\\Users\\me\\heat", "c:/users/me/heat/", true));
  assert.ok(!sameFolder("/home/me/Heat", "/home/me/heat", false));
});
