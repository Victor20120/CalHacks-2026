// This client only talks to ExpandRG. CREAO keys stay on the backend.
import { call, HttpError } from "./http";

export interface AgentSettings {
  backendUrl: string;
  connectorUrl: string;
  member: string;
  project: string;
}
export interface AgentIdentity { id: string; number: number; name: string; }
export interface AgentSession extends AgentSettings {
  token: string;
  memberName: string;
  projectName: string;
  agent?: AgentIdentity;
}
export interface Exchange { id: string; question: string; reply: string; agent?: AgentIdentity; }
export interface ProjectMap {
  project: string;
  tree: string;
  files: string[];
  excerpts: { path: string; kind: string; excerpt: string; truncated: boolean }[];
  stats: Record<string, unknown>;
  total_chars: number;
  redactions: number;
}
export interface AgentJob {
  job_id: string;
  kind: string;
  status: "running" | "completed" | "failed";
  created_at: string;
  diagnosis?: { reply: string };
  error?: { message: string };
  warning?: string;
}
export interface StartJob { job_id: string; instructions?: string | null; }
export interface AgentData {
  agent?: AgentIdentity;
  exchanges: Exchange[];
  instructions: string;
  defaults: string;
  overview: string | null;
  pendingJobId: string | null;
}
type Requester = <T>(base: string, path: string, options?: {
  method?: string; body?: unknown; token?: string; timeoutMs?: number;
}) => Promise<T>;

function personalChatError(error: unknown): never {
  if (error instanceof HttpError && error.status === 404) {
    throw new Error("The running ExpandRG backend does not have personal chat. Restart ExpandRG using run_demo.py, then click Refresh here.");
  }
  throw error;
}

export class PersonalAgentClient {
  constructor(private readonly request: Requester = call) {}

  async signIn(settings: AgentSettings): Promise<AgentSession> {
    const auth = await this.request<{ token: string; member: { display_name: string; role: string } }>(
      settings.backendUrl, "/sign-in", { method: "POST", body: { member_id: settings.member } });
    if (auth.member.role !== "researcher") throw new Error("Choose a researcher profile to talk to a personal agent.");
    const me = await this.request<{ projects: { id: string; name: string }[]; agent?: AgentIdentity }>(settings.backendUrl, "/me", { token: auth.token });
    const project = me.projects.find((p) => p.id === settings.project);
    if (!project) throw new Error("Choose an ExpandRG project you belong to, then reopen your personal agent.");
    return { ...settings, token: auth.token, memberName: auth.member.display_name, projectName: project.name, agent: me.agent };
  }

  async load(session: AgentSession): Promise<AgentData> {
    const base = `/projects/${encodeURIComponent(session.project)}`;
    const [chat, rules, overview] = await Promise.all([
      this.request<{ exchanges: Exchange[]; pending_job_id: string | null; agent?: AgentIdentity }>(session.backendUrl, `${base}/personal-chat`, { token: session.token })
        .catch(personalChatError),
      this.request<{ instructions: string; default_instructions: string }>(session.backendUrl, `${base}/agent-rules`, { token: session.token }),
      this.request<{ reply: string }>(session.backendUrl, `${base}/overview`, { token: session.token })
        .catch((e) => { if (e instanceof HttpError && e.status === 404) return null; throw e; }),
    ]);
    return { agent: chat.agent ?? session.agent, exchanges: chat.exchanges, pendingJobId: chat.pending_job_id, instructions: rules.instructions,
      defaults: rules.default_instructions, overview: overview?.reply ?? null };
  }

  // folder: the granted project folder's name (never its path), so the agent can read its files.
  send(session: AgentSession, message: string, folder?: string): Promise<StartJob> {
    return this.request<StartJob>(session.backendUrl, "/personal-agent/chat", { method: "POST", token: session.token,
      body: { project_id: session.project, message, ...(folder ? { folder } : {}) } }).catch(personalChatError);
  }
  saveRules(session: AgentSession, instructions: string): Promise<{ instructions: string }> {
    return this.request(session.backendUrl, `/projects/${encodeURIComponent(session.project)}/agent-rules`,
      { method: "PUT", token: session.token, body: { instructions } });
  }
  clear(session: AgentSession): Promise<unknown> {
    return this.request(session.backendUrl, `/projects/${encodeURIComponent(session.project)}/personal-chat`,
      { method: "DELETE", token: session.token });
  }
  job(session: AgentSession, id: string): Promise<AgentJob> {
    return this.request(session.backendUrl, `/jobs/${encodeURIComponent(id)}`, { token: session.token });
  }
  map(session: AgentSession, root: string): Promise<ProjectMap> {
    return this.request(session.connectorUrl, "/project-map", { method: "POST", body: { root }, timeoutMs: 30000 });
  }
  overview(session: AgentSession, map: ProjectMap): Promise<StartJob> {
    const { tree, files, excerpts, stats } = map;
    return this.request(session.backendUrl, "/overview", { method: "POST", token: session.token,
      body: { project_id: session.project, tree, files, excerpts, stats } });
  }
}
