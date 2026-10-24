import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { PersonalAgentClient, AgentSettings, AgentSession, AgentData, ProjectMap } from "./personalAgentClient";
import { personalAgentHtml } from "./personalAgentHtml";

interface Deps {
  settings(): AgentSettings;
  ensureProject(): Promise<boolean>;
  pickFolder(): Promise<vscode.WorkspaceFolder | undefined>;
  ensureGranted(folder: string): Promise<string | undefined>;
}
const emptyData = (): AgentData => ({ exchanges: [], instructions: "", defaults: "", overview: null, pendingJobId: null });

export class PersonalAgentPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private session?: AgentSession;
  private data = emptyData();
  private preview?: ProjectMap;
  private pending?: { id: string; kind: "personal_chat" | "overview"; question?: string };
  private busy = false;
  private error = "";
  private notice = "";
  private generation = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private readonly configListener: vscode.Disposable;

  constructor(private readonly deps: Deps, private readonly client = new PersonalAgentClient()) {
    this.configListener = vscode.workspace.onDidChangeConfiguration((e) => {
      if (this.panel && ["expandrg.member", "expandrg.project", "expandrg.backendUrl", "expandrg.connectorUrl"]
        .some((key) => e.affectsConfiguration(key))) void this.load();
    });
  }

  async show(): Promise<void> {
    if (!(await this.deps.ensureProject())) return;
    if (this.panel) { this.panel.reveal(vscode.ViewColumn.Beside); return; }
    const panel = vscode.window.createWebviewPanel("expandrg.personalAgent", "ExpandRG AI", vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [] });
    this.panel = panel;
    panel.onDidDispose(() => { if (this.panel === panel) { this.reset(); this.panel = undefined; } });
    panel.webview.onDidReceiveMessage((message) => { void this.handle(message); });
    panel.webview.html = personalAgentHtml(randomBytes(20).toString("hex"));
    await this.load();
  }

  dispose(): void {
    this.configListener.dispose();
    this.reset();
    this.panel?.dispose();
    this.panel = undefined;
  }

  private reset(): number {
    this.generation++;
    clearTimeout(this.timer);
    this.session = undefined;
    this.data = emptyData();
    this.preview = undefined;
    this.pending = undefined;
    this.busy = false;
    this.error = this.notice = "";
    return this.generation;
  }

  private current(generation: number): boolean { return !!this.panel && this.generation === generation; }

  private render(extra: Record<string, unknown> = {}): void {
    if (!this.panel) return;
    const session = this.session, map = this.preview;
    const agentName = this.data.agent?.name || session?.agent?.name || "Personal agent";
    const preview = map && session ? JSON.stringify({ project_id: session.project, tree: map.tree,
      files: map.files, excerpts: map.excerpts, stats: map.stats }, null, 2) : "";
    void this.panel.webview.postMessage({ type: "state", ready: !!session, busy: this.busy,
      agentName,
      identity: session ? `${session.memberName} · ${session.projectName}` : "Connecting to ExpandRG AI…",
      exchanges: this.data.exchanges, rules: this.data.instructions, defaults: this.data.defaults,
      overview: this.data.overview, pending: !!this.pending, pendingQuestion: this.pending?.question,
      error: this.error, notice: this.notice, preview,
      previewStats: map ? `${map.files.length} files mapped · ${map.total_chars} characters of project context · ${map.redactions} secrets blanked. Review before sending to CREAO.` : "",
      ...extra });
  }

  private async load(): Promise<void> {
    const generation = this.reset();
    this.busy = true;
    this.render({ forceRules: true, clearMessage: true });
    try {
      const session = await this.client.signIn({ ...this.deps.settings() });
      if (!this.current(generation)) return;
      const data = await this.client.load(session);
      if (!this.current(generation)) return;
      this.session = session;
      this.data = data;
      if (this.panel) this.panel.title = `ExpandRG AI · ${data.agent?.name || session.agent?.name || "Personal agent"}`;
      if (data.pendingJobId) {
        this.pending = { id: data.pendingJobId, kind: "personal_chat" };
        this.render({ forceRules: true });
        void this.poll(session, generation);
      } else {
        this.busy = false;
        this.render({ forceRules: true });
      }
    } catch (e) {
      if (this.current(generation)) { this.busy = false; this.error = this.message(e); this.render(); }
    }
  }

  private async handle(message: unknown): Promise<void> {
    if (!message || typeof message !== "object") return;
    const value = message as Record<string, unknown>;
    if (value.action === "ready") { this.render(); return; }
    if (value.action === "refresh" && !this.busy) { await this.load(); return; }
    if (!this.session || this.busy) return;
    if (value.action === "discardMap") { this.preview = undefined; this.render(); return; }
    if (!["send", "saveRules", "clear", "map", "sendMap"].includes(String(value.action))) return;
    const session = this.session, generation = this.generation;
    this.busy = true;
    this.error = this.notice = "";
    this.render();
    try {
      if (value.action === "send") {
        if (typeof value.message !== "string" || !value.message.trim() || value.message.length > 4000) throw new Error("Enter a message of up to 4,000 characters.");
        const job = await this.client.send(session, value.message.trim());
        if (!this.current(generation)) return;
        if (job.instructions != null) this.data.instructions = job.instructions;
        this.pending = { id: job.job_id, kind: "personal_chat", question: value.message.trim() };
        this.render({ clearMessage: true, forceRules: job.instructions != null });
        void this.poll(session, generation);
      } else if (value.action === "saveRules") {
        if (typeof value.instructions !== "string" || value.instructions.length > 2000) throw new Error("Use at most 2,000 characters for instructions.");
        const result = await this.client.saveRules(session, value.instructions);
        if (!this.current(generation)) return;
        this.data.instructions = result.instructions;
        this.notice = "Instructions saved for your next messages, diagnoses, and project overviews.";
      } else if (value.action === "clear") {
        await this.client.clear(session);
        if (!this.current(generation)) return;
        this.data.exchanges = [];
        this.notice = "Personal conversation cleared. Instructions and project overview are kept.";
      } else if (value.action === "map") {
        if (!vscode.workspace.isTrusted) throw new Error("Trust this workspace before reading project files.");
        const folder = await this.deps.pickFolder();
        if (!folder || !this.current(generation)) return;
        const grant = await this.deps.ensureGranted(folder.uri.fsPath);
        if (!grant || !this.current(generation)) return;
        const map = await this.client.map(session, folder.uri.fsPath);
        if (!this.current(generation)) return;
        this.preview = map;
        this.notice = "The project map is ready to review. Nothing has been sent to CREAO yet.";
      } else if (value.action === "sendMap") {
        if (!this.preview) throw new Error("Map your project and review the context first.");
        const job = await this.client.overview(session, this.preview);
        if (!this.current(generation)) return;
        this.preview = undefined;
        this.pending = { id: job.job_id, kind: "overview", question: "Understand this project and how to build and run it." };
        this.render();
        void this.poll(session, generation);
      }
    } catch (e) {
      if (this.current(generation)) this.error = this.message(e);
    } finally {
      if (this.current(generation)) {
        if (!this.pending) this.busy = false;
        this.render({ forceRules: value.action === "saveRules" && !this.error });
      }
    }
  }

  private async poll(session: AgentSession, generation: number, failures = 0): Promise<void> {
    const pending = this.pending;
    if (!pending || !this.current(generation)) return;
    try {
      const job = await this.client.job(session, pending.id);
      if (!this.current(generation)) return;
      if (job.kind !== pending.kind) throw new Error("The response belongs to a different workflow. Refresh to reconnect.");
      if (job.status === "running") {
        this.timer = setTimeout(() => { void this.poll(session, generation); }, 1200);
        return;
      }
      if (job.status === "failed") {
        this.pending = undefined;
        this.busy = false;
        this.error = job.error?.message || "Your agent could not finish. You can try again.";
        this.render({ restoreMessage: pending.kind === "personal_chat" ? pending.question : undefined });
        return;
      }
      const data = await this.client.load(session);
      if (!this.current(generation)) return;
      if (job.warning && pending.kind === "personal_chat") data.exchanges.push({
        id: job.job_id, question: pending.question || "Your message", reply: job.diagnosis?.reply || "" });
      this.data = data;
      this.pending = undefined;
      this.busy = false;
      this.notice = job.warning || (pending.kind === "overview" ? "Project overview saved. Ask your agent to help refine the build and simulation setup." : "");
      this.render({ forceRules: true });
    } catch (e) {
      if (!this.current(generation)) return;
      if (failures < 2) {
        this.timer = setTimeout(() => { void this.poll(session, generation, failures + 1); }, 2000);
      } else {
        this.pending = undefined;
        this.busy = false;
        this.error = `${this.message(e)} Refresh to reconnect; the CREAO run may still finish. It has not been restarted.`;
        this.render();
      }
    }
  }

  private message(error: unknown): string { return error instanceof Error ? error.message : String(error); }
}
