import { strict as assert } from "node:assert";
import { test } from "node:test";
import { PersonalAgentClient, AgentSettings } from "../personalAgentClient";
import { HttpError } from "../http";
import { personalAgentHtml } from "../personalAgentHtml";

test("personal requests carry the selected user's token and never use the shared hub route", async () => {
  const calls: { base: string; path: string; options: any }[] = [];
  const request = async <T>(base: string, path: string, options: any = {}): Promise<T> => {
    calls.push({ base, path, options });
    if (path === "/sign-in") return { token: `private-${options.body.member_id}`, member: { role: "researcher", display_name: options.body.member_id } } as T;
    if (path === "/me") {
      const number = options.token === "private-b" ? 2 : 1;
      return { projects: [{ id: "heat", name: "Heat" }], agent: { id: `personal-${number}`, number, name: `Agent ${number}` } } as T;
    }
    if (path.endsWith("/personal-chat")) return { exchanges: [], pending_job_id: null } as T;
    if (path.endsWith("/agent-rules")) return { instructions: "Be clear", default_instructions: "Be professional" } as T;
    if (path.endsWith("/overview")) throw new HttpError("No overview", 404);
    return { job_id: "personal-job" } as T;
  };
  const client = new PersonalAgentClient(request);
  const settings: AgentSettings = { member: "a", project: "heat", backendUrl: "http://backend", connectorUrl: "http://connector" };
  const a = await client.signIn(settings), b = await client.signIn({ ...settings, member: "b" });
  assert.equal(a.agent?.name, "Agent 1");
  assert.equal(b.agent?.name, "Agent 2");
  await client.send(a, "A private question");
  await client.send(b, "B private question");
  const sends = calls.filter((c) => c.path === "/personal-agent/chat");
  assert.equal(sends[0].options.token, "private-a");
  assert.equal(sends[1].options.token, "private-b");
  assert.deepEqual(sends[0].options.body, { project_id: "heat", message: "A private question" });
  await client.send(a, "With files", "sample_project");
  assert.deepEqual(calls.at(-1)!.options.body, { project_id: "heat", message: "With files", folder: "sample_project" });
  assert.ok(!calls.some((c) => c.path === "/superbrain/chat" || c.path === "/agent/chat"));
  assert.equal((await client.load(a)).overview, null);
  assert.equal((await client.load(a)).instructions, "Be clear");
  assert.equal((await client.load(a)).agent?.name, "Agent 1");
});

test("project context uses the connector, and overview submission excludes local root and extra fields", async () => {
  const calls: { base: string; path: string; options: any }[] = [];
  const map = { project: "local-name", tree: "src/", files: ["src/a.f90"], excerpts: [], stats: { files: 1 }, total_chars: 20, redactions: 0,
    root: "C:\\private\\researcher\\project", extra: "not part of the provider payload" };
  const client = new PersonalAgentClient(async <T>(base: string, path: string, options: any = {}) => {
    calls.push({ base, path, options });
    return (path === "/project-map" ? map : { job_id: "overview" }) as T;
  });
  const session = { member: "a", project: "heat", backendUrl: "http://backend", connectorUrl: "http://connector",
    token: "private-token", memberName: "A", projectName: "Heat" };
  const result = await client.map(session, map.root);
  assert.equal(calls.length, 1); // preparing the map does not start an overview
  assert.equal(calls[0].base, "http://connector");
  assert.equal(calls[0].options.token, undefined);
  await client.overview(session, result);
  assert.deepEqual(calls[1].options.body, { project_id: "heat", tree: "src/", files: ["src/a.f90"], excerpts: [], stats: { files: 1 } });
  assert.equal(calls[1].options.token, "private-token");
});

test("personal agent client refuses leaders and does not treat a denied overview as absent", async () => {
  const leader = new PersonalAgentClient(async <T>() => ({ token: "t", member: { role: "leader" } } as T));
  await assert.rejects(leader.signIn({ member: "leader", project: "heat", backendUrl: "http://backend", connectorUrl: "http://connector" }), /researcher/);
  const client = new PersonalAgentClient(async <T>(_base: string, path: string) => {
    if (path.endsWith("/overview")) throw new HttpError("Access removed", 403);
    return { exchanges: [], pending_job_id: null, instructions: "", default_instructions: "" } as T;
  });
  await assert.rejects(client.load({ member: "a", project: "heat", backendUrl: "http://backend", connectorUrl: "http://connector",
    token: "t", memberName: "A", projectName: "Heat" }), /Access removed/);
});

test("webview blocks external resources and treats conversation output as plain text", () => {
  const html = personalAgentHtml("safe123nonce");
  assert.ok(html.includes("default-src 'none'"));
  assert.ok(html.includes("script-src 'nonce-safe123nonce'"));
  assert.ok(html.includes("textContent=x.reply"));
  assert.ok(!html.includes("innerHTML"));
  assert.throws(() => personalAgentHtml('bad" nonce'), /Invalid webview nonce/);
});

test("an older backend gives a restart hint for missing personal chat while other errors remain intact", async () => {
  const session = { member: "a", project: "heat", backendUrl: "http://backend", connectorUrl: "http://connector",
    token: "private-token", memberName: "A", projectName: "Heat" };
  const client = new PersonalAgentClient(async <T>(_base: string, path: string) => {
    if (path.endsWith("/personal-chat") || path === "/personal-agent/chat") throw new HttpError("Not Found", 404);
    return { instructions: "Be clear", default_instructions: "Be clear" } as T;
  });
  await assert.rejects(client.load(session), /Restart ExpandRG using run_demo.py, then click Refresh/);
  await assert.rejects(client.send(session, "Hello"), /Restart ExpandRG/);
  const denied = new PersonalAgentClient(async <T>(): Promise<T> => { throw new HttpError("Membership removed", 403); });
  await assert.rejects(denied.send(session, "Hello"), /Membership removed/);
});
