import { strict as assert } from "node:assert";
import { test } from "node:test";
import type { PersonalAgentClient, AgentSettings, AgentSession } from "../personalAgentClient";

const moduleLoader = require("node:module") as { _load: (...args: any[]) => any };
const originalLoad = moduleLoader._load;
let onMessage: (message: unknown) => void;
let onConfig: (event: { affectsConfiguration(key: string): boolean }) => void;
let states: any[] = [];
const vscode = {
  ViewColumn: { Beside: 2 },
  workspace: { isTrusted: true, onDidChangeConfiguration: (fn: typeof onConfig) => { onConfig = fn; return { dispose() {} }; } },
  window: { createWebviewPanel: () => ({
    webview: { html: "", postMessage: (state: unknown) => { states.push(state); return Promise.resolve(true); },
      onDidReceiveMessage: (fn: typeof onMessage) => { onMessage = fn; } },
    onDidDispose() {}, reveal() {}, dispose() {},
  }) },
};
moduleLoader._load = function (request: string, ...args: any[]) {
  return request === "vscode" ? vscode : originalLoad.call(this, request, ...args);
};
const { PersonalAgentPanel } = require("../personalAgent") as typeof import("../personalAgent");
moduleLoader._load = originalLoad;
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise<void>((r) => setImmediate(r)); };

function harness(overrides: Record<string, unknown> = {}) {
  states = [];
  const settings: AgentSettings = { member: "a", project: "heat", backendUrl: "http://backend", connectorUrl: "http://connector" };
  const client: any = {
    signIn: async (s: AgentSettings) => ({ ...s, token: `private-auth-${s.member}`, memberName: s.member, projectName: s.project,
      agent: { id: `agent-${s.member}`, number: s.member === "a" ? 1 : 2, name: s.member === "a" ? "Agent 1" : "Agent 2" } }),
    load: async (s: AgentSession) => ({ exchanges: [{ id: "1", question: `${s.member} only`, reply: "private reply" }],
      instructions: "Be clear", defaults: "Be professional", overview: null, pendingJobId: null }),
    ...overrides,
  };
  const panel = new PersonalAgentPanel({ settings: () => ({ ...settings }), ensureProject: async () => true,
    pickFolder: async () => ({ uri: { fsPath: "/project" } } as any), ensureGranted: async () => "/project",
    chatFolder: async () => overrides.folder as string | undefined },
    client as PersonalAgentClient);
  return { settings, client, panel };
}

test("a late response from A cannot appear after switching the panel to B", async () => {
  let resolve!: (value: unknown) => void;
  let sentAs = "";
  const h = harness({ send: (session: AgentSession) => { sentAs = session.member; return new Promise((r) => { resolve = r; }); } });
  await h.panel.show();
  onMessage({ action: "send", message: "A_PRIVATE_MESSAGE" });
  await flush();
  h.settings.member = "b";
  onConfig({ affectsConfiguration: (key) => key === "expandrg.member" });
  await flush();
  resolve({ job_id: "a-job" });
  await flush();
  const last = states.at(-1);
  assert.equal(sentAs, "a");
  assert.equal(last.identity, "b · heat");
  assert.equal(last.agentName, "Agent 2");
  assert.equal(last.exchanges[0].question, "b only");
  assert.equal(last.pendingQuestion, undefined);
  assert.equal(last.busy, false);
  assert.ok(!JSON.stringify(states).includes("private-auth"));
  h.panel.dispose();
});

test("understanding a project waits for preview approval before a provider request", async () => {
  let sends = 0;
  const h = harness({
    map: async () => ({ project: "project", tree: "project/", files: ["solver.f90"], excerpts: [], stats: {}, total_chars: 100, redactions: 0 }),
    overview: async () => { sends++; return { job_id: "overview-job" }; },
    job: async () => ({ job_id: "overview-job", kind: "overview", status: "completed", diagnosis: { reply: "Fortran solver" } }),
  });
  await h.panel.show();
  onMessage({ action: "map" });
  await flush();
  assert.equal(sends, 0);
  assert.ok(states.at(-1).preview.includes("solver.f90"));
  onMessage({ action: "sendMap", command: "do not execute this untrusted extra field" });
  await flush();
  assert.equal(sends, 1);
  assert.equal(states.at(-1).busy, false);
  assert.ok(states.at(-1).notice.includes("Project overview saved"));
  h.panel.dispose();
});

test("reopening reconnects to a pending personal reply without sending another message", async () => {
  let sends = 0;
  const h = harness({
    load: async () => ({ exchanges: [], instructions: "clear", defaults: "clear", overview: null, pendingJobId: "existing-job" }),
    send: async () => { sends++; },
    job: async () => ({ job_id: "existing-job", kind: "personal_chat", status: "failed", error: { message: "Provider timeout" } }),
  });
  await h.panel.show();
  await flush();
  assert.equal(sends, 0);
  assert.equal(states.at(-1).error, "Provider timeout");
  assert.equal(states.at(-1).busy, false);
  h.panel.dispose();
});

test("the agent is told which granted folder it may read, by name only", async () => {
  let sent: unknown[] = [];
  const h = harness({
    folder: "sample_project",
    send: async (_session: AgentSession, message: string, folder?: string) => { sent = [message, folder]; return { job_id: "j" }; },
    job: async () => ({ job_id: "j", kind: "personal_chat", status: "completed", diagnosis: { reply: "ok" } }),
  });
  await h.panel.show();
  await flush();
  assert.equal(states.at(-1).identity, "a · heat · can read sample_project");
  onMessage({ action: "send", message: "Why does it blow up?" });
  await flush();
  assert.deepEqual(sent, ["Why does it blow up?", "sample_project"]);
  h.panel.dispose();
});

test("simulation setup prepares a project inspection request and waits for Send", async () => {
  const sent: { message: string; folder?: string }[] = [];
  const h = harness({
    folder: "fortran-study",
    send: async (_session: AgentSession, message: string, folder?: string) => {
      sent.push({ message, folder }); return { job_id: "setup-chat" };
    },
    job: async () => ({ job_id: "setup-chat", kind: "personal_chat", status: "completed", diagnosis: { reply: "Build, then run." } }),
  });
  await h.panel.showSimulationSetup();
  const draft = states.at(-1).composeRequest;
  assert.ok(draft.text.includes("First inspect the actual project"));
  assert.ok(draft.text.includes("For Python"));
  assert.ok(draft.text.includes("Fortran"));
  assert.ok(draft.text.includes("separate compilation/build commands"));
  assert.equal(sent.length, 0);
  // The webview may become ready after the initial state was posted.
  onMessage({ action: "ready" });
  await flush();
  assert.equal(states.at(-1).composeRequest.id, draft.id);
  onMessage({ action: "setupDraftHandled", id: draft.id, applied: true });
  await flush();
  assert.equal(states.at(-1).composeRequest, undefined);
  assert.ok(states.at(-1).notice.includes("then Send"));
  onMessage({ action: "send", message: draft.text });
  await flush();
  assert.deepEqual(sent, [{ message: draft.text, folder: "fortran-study" }]);
  h.panel.dispose();
});

test("setup does not replace an existing draft and is discarded on a profile switch", async () => {
  const h = harness();
  await h.panel.showSimulationSetup();
  const draft = states.at(-1).composeRequest;
  onMessage({ action: "setupDraftHandled", id: draft.id, applied: false });
  await flush();
  assert.ok(states.at(-1).notice.includes("current draft is kept"));
  await h.panel.showSimulationSetup();
  assert.ok(states.at(-1).composeRequest);
  h.settings.member = "b";
  onConfig({ affectsConfiguration: (key) => key === "expandrg.member" });
  await flush();
  assert.equal(states.at(-1).composeRequest, undefined);
  assert.equal(states.at(-1).agentName, "Agent 2");
  h.panel.dispose();
});
