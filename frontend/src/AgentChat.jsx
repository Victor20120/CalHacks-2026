import { useEffect, useRef, useState } from "react";
import { backend } from "./api.js";
import { ErrorNote, Markdown } from "./components.jsx";

export default function AgentChat({ session, project, onOpenFinding }) {
  const [scope, setScope] = useState("project"); // the project chosen in the top bar
  return <ChatPanel key={scope} session={session} project={project} onOpenFinding={onOpenFinding} scope={scope} onScope={setScope} />;
}

function ChatPanel({ session, project, onOpenFinding, scope, onScope }) {
  const [exchanges, setExchanges] = useState([]);
  const [question, setQuestion] = useState("");
  const [rules, setRules] = useState("");
  const [savedRules, setSavedRules] = useState("");
  const [defaults, setDefaults] = useState("");
  const [editing, setEditing] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const scroller = useRef(null);
  const token = session.token;
  const base = `/projects/${encodeURIComponent(project.id)}`;
  const chatPath = `${base}/chat?scope=${scope}`;

  useEffect(() => {
    let stopped = false;
    Promise.all([backend(chatPath, { token }), backend(`${base}/agent-rules`, { token })])
      .then(([chat, settings]) => {
        if (stopped) return;
        setExchanges(chat.exchanges);
        setRules(settings.instructions);
        setSavedRules(settings.instructions);
        setDefaults(settings.default_instructions);
        if (chat.pending_job_id) setPending({ jobId: chat.pending_job_id, startedAt: Date.now() });
        setLoaded(true);
      })
      .catch((e) => { if (!stopped) setError(e.message); });
    return () => { stopped = true; };
  }, [base, chatPath, token]);

  useEffect(() => {
    if (!pending) { setElapsed(0); return undefined; }
    const update = () => setElapsed(Math.floor((Date.now() - pending.startedAt) / 1000));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [pending]);

  useEffect(() => {
    if (!pending) return undefined;
    let stopped = false;
    let timer;
    async function poll() {
      try {
        const job = await backend(`/jobs/${pending.jobId}`, { token });
        if (stopped) return;
        if (job.status === "running") {
          timer = setTimeout(poll, 750);
          return;
        }
        if (job.status === "failed") {
          setError(job.error?.message || "Your agent could not reply. Try again.");
          if (pending.question) setQuestion(pending.question);
        } else {
          const chat = await backend(chatPath, { token });
          if (stopped) return;
          setExchanges(job.warning ? [...chat.exchanges, {
            id: job.job_id, question: pending.question || "Your message", ...job.diagnosis,
            sources: job.prior_findings,
          }] : chat.exchanges);
          setNotice(job.warning || "");
        }
        setPending(null);
      } catch (e) {
        if (stopped) return;
        setError(`${e.message} Reload this page to reconnect to your conversation.`);
        // Keep sending disabled: a network interruption does not cancel a CREAO run.
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [pending, chatPath, token]);

  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [exchanges, pending]);

  async function ask(event) {
    event.preventDefault();
    const message = question.trim();
    if (!message || pending || busy || !loaded || rules !== savedRules) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const job = await backend("/superbrain/chat", { method: "POST", token, body: { project_id: project.id, message, scope } });
      if (job.instructions !== null && job.instructions !== undefined) {
        setRules(job.instructions);
        setSavedRules(job.instructions);
        setNotice("Instructions saved. Super Brain is replying with your updated preferences.");
      }
      setPending({ jobId: job.job_id, question: message, sources: job.prior_findings,
        stats: job.knowledge_stats, startedAt: Date.now() });
      setQuestion("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveRules() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await backend(`${base}/agent-rules`, { method: "PUT", token, body: { instructions: rules } });
      setRules(result.instructions);
      setSavedRules(result.instructions);
      setNotice("Instructions saved. They apply to your next message, diagnosis, and project overview.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    setError("");
    try {
      await backend(chatPath, { method: "DELETE", token });
      setExchanges([]);
      setNotice("Conversation cleared in ExpandRG. Saved instructions are kept.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const dirty = rules !== savedRules;
  return (
    <section className="agent-chat" aria-label="Super Brain research assistant">
      <div className="agent-chat-head">
        <div className="agent-chat-title">
          <strong>Ask Super Brain</strong>
          <label className="agent-scope">Knowledge
            <select aria-label="Knowledge scope" value={scope} onChange={(e) => onScope(e.target.value)} disabled={busy || !!pending}>
              <option value="project">{project.name}</option>
              <option value="all">All my projects</option>
            </select>
          </label>
        </div>
        <div className="agent-chat-actions">
          <button className="link" onClick={() => setEditing(!editing)} aria-expanded={editing} aria-controls="agent-instructions">
            Instructions{dirty ? " *" : ""}
          </button>
          <button className="link" onClick={clear} disabled={!loaded || busy || !!pending || !exchanges.length}>Clear chat</button>
        </div>
      </div>
      {editing && (
        <div id="agent-instructions" className="agent-instructions">
          <label htmlFor="agent-rules">How should Super Brain respond?</label>
          <textarea id="agent-rules" value={rules} onChange={(e) => setRules(e.target.value)} maxLength={2000} rows={3}
            disabled={!loaded || busy} placeholder="Keep responses professional and focused on research. Avoid jokes." />
          <div className="agent-rule-actions">
            <span className="muted small">Saved for you in this project · {rules.length}/2000</span>
            <button type="button" disabled={!loaded || busy} onClick={() => setRules(defaults)}>Use defaults</button>
            <button className="primary" type="button" onClick={saveRules} disabled={!loaded || busy || !dirty}>Save instructions</button>
          </div>
        </div>
      )}
      {(exchanges.length > 0 || pending) && (
        <div className="agent-transcript" ref={scroller} role="log" aria-label="Agent conversation" aria-live="polite">
          {exchanges.map((x) => (
            <article className="exchange" key={x.id}>
              <p className="question"><span className="label">You</span> {x.question}</p>
              <div className="agent-reply"><span className="label">{x.reply_kind === "creao" || !x.reply_kind ? "Super Brain · CREAO" : "Super Brain"}</span><Markdown text={x.reply} /></div>
              {x.elapsed_ms !== undefined && <p className="muted small agent-answer-meta">
                {x.reply_kind === "creao" ? `Answered in ${(x.elapsed_ms / 1000).toFixed(1)}s` : "Answered without a CREAO run"}
                {x.knowledge_stats?.findings !== undefined && ` · ${x.knowledge_stats.findings} shared findings available from ${x.knowledge_stats.researchers} researchers`}
              </p>}
              {x.sources?.length > 0 && <div className="agent-sources"><span className="muted small">Shared knowledge: </span>
                {x.sources.map((f) => <button className="link" key={f.id} onClick={() => onOpenFinding(f.id)}>{f.contributed_by_name} · {f.title} ({f.verification_status})</button>)}
              </div>}
            </article>
          ))}
          {pending && <div className="exchange">
            {pending.question && <p className="question"><span className="label">You</span> {pending.question}</p>}
            <p className="muted" role="status">Super Brain is thinking… {elapsed}s elapsed.</p>
            {pending.stats && <p className="muted small">{pending.sources?.length || 0} shared findings retrieved from {pending.stats.findings} available records. CREAO uses these alongside its own reasoning.</p>}
            {pending.sources?.length > 0 && <div className="agent-sources">
              {pending.sources.map((f) => <button className="link" key={f.id} onClick={() => onOpenFinding(f.id)}>{f.title} · {f.contributed_by_name}</button>)}
            </div>}
          </div>}
        </div>
      )}
      <div className="agent-feedback"><ErrorNote error={error} />{notice && <p className="muted small" role="status">{notice}</p>}</div>
      <form className="ask-dock" onSubmit={ask}>
        <div className="ask-row">
          <input value={question} maxLength={4000} onChange={(e) => setQuestion(e.target.value)}
            placeholder={loaded ? "Ask what the group knows, or say: Act as a researcher and be clear…" : "Loading shared knowledge…"}
            aria-label="Message your research agent" disabled={!loaded || busy} />
          <button className="primary" type="submit" disabled={!loaded || busy || !!pending || !question.trim() || dirty}>
            {pending || busy ? "Please wait…" : "Send"}
          </button>
        </div>
        <p className="muted small">{dirty ? "Save your instructions before sending. " : ""}
          CREAO answers using its own knowledge and research shared by your group's agents. You can set response instructions directly in chat. Sending uses CREAO credits.
        </p>
      </form>
    </section>
  );
}
