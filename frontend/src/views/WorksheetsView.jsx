import { useEffect, useRef, useState } from "react";
import { Plus, Sparkles, X } from "lucide-react";
import { backend } from "../api.js";
import { ErrorNote } from "../components.jsx";

const LINKED = new URLSearchParams(window.location.search).get("worksheet");

const STATUS = {
  leader: { assigned: "Waiting for answers", submitted: "Ready to check", reviewed: "Checked" },
  researcher: { assigned: "To do", submitted: "Sent to the leader", reviewed: "Checked by the leader" },
};

const MAX_QUESTIONS = 8;

// The Super Brain's teacher: the leader makes worksheets for the researchers they pick, by asking
// the Super Brain or by writing the questions for it to finish; each researcher's are numbered
// Worksheet 1, 2, ... Researchers answer; the leader reads the answers and comments.
export default function WorksheetsView({ session, project, onOpenFinding }) {
  const [list, setList] = useState(null);
  const [openId, setOpenId] = useState(LINKED); // ?worksheet=<id> opens one
  const [composing, setComposing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const shownProject = useRef(project.id);
  const role = session.member.role === "leader" ? "leader" : "researcher";

  function load() {
    return backend(`/worksheets?project_id=${encodeURIComponent(project.id)}`, { token: session.token })
      .then((rows) => {
        setList(rows);
        setError("");
        return rows;
      })
      .catch((e) => {
        setError(e.message);
        return [];
      });
  }

  useEffect(() => {
    if (project.id !== shownProject.current) setOpenId(null); // a worksheet belongs to one project
    shownProject.current = project.id;
    load();
  }, [project.id]);

  if (openId) {
    return (
      <Worksheet session={session} id={openId} role={role} onOpenFinding={onOpenFinding}
        onBack={() => { setOpenId(null); load(); }} />
    );
  }

  async function act(path) {
    try {
      await backend(path, { method: "POST", token: session.token });
      await load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function made(ids) {
    const rows = await load();
    const madeNow = rows.filter((w) => ids.includes(w.id));
    setNotice(madeNow.length
      ? `Ready: ${madeNow.map((w) => `Worksheet ${w.number} for ${w.member_name}`).join(", ")}.`
      : "CREAO didn't return a usable worksheet. Try again.");
    if (madeNow.length) setComposing(false);
  }

  return (
    <div>
      <div className="worksheets-head">
        <div>
          <h2>Worksheets</h2>
          <p className="muted">
            {role === "leader"
              ? `CREAO writes worksheets for the researchers in ${project.name}: questions and things to try. Ask it what to cover, or write the questions yourself and it finishes them. Each person's are numbered Worksheet 1, 2, and so on. You read their answers and comment.`
              : `Questions and things to try, written by CREAO for you from what others in ${project.name} shared. Your leader reads your answers and comments.`}
          </p>
        </div>
        {role === "leader" && !composing && (
          <button className="primary" onClick={() => { setComposing(true); setNotice(""); }}><Plus /> New worksheet</button>
        )}
      </div>
      <ErrorNote error={error} />
      {role === "leader" && composing && (
        <NewWorksheet session={session} project={project} onMade={made} onClose={() => setComposing(false)} />
      )}
      {notice && <p className="success-note" role="status">{notice}</p>}

      {list && !list.length ? (
        <section className="card">
          <p className="muted">
            {role === "leader" ? "No worksheets yet. Click New worksheet to make the first one." : "No worksheets for you yet."}
          </p>
        </section>
      ) : list && (
        <section className="card">
          <h3>{role === "leader" ? "All worksheets" : "Your worksheets"}</h3>
          <table className="worksheet-table">
            <thead>
              <tr>
                {role === "leader" && <th>Researcher</th>}
                <th>Worksheet</th>
                <th>Made</th>
                <th>Status</th>
                <th>Answered</th>
                {role === "leader" && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {list.map((w) => (
                <tr key={w.id} className={w.cancelled ? "cancelled" : ""}>
                  {role === "leader" && <td className="nowrap">{w.member_name}</td>}
                  <td>
                    <button className="link worksheet-link" onClick={() => setOpenId(w.id)}>
                      <strong>Worksheet {w.number}</strong><span className="muted"> · {w.title}</span>
                    </button>
                  </td>
                  <td className="nowrap muted">{w.created_at.slice(0, 10)}</td>
                  <td className="nowrap">
                    {w.cancelled
                      ? <span className="tag worksheet-cancelled">Cancelled</span>
                      : <span className={`tag worksheet-${w.status}`}>{STATUS[role][w.status]}</span>}
                  </td>
                  <td className="nowrap">{w.answered} of {w.items}</td>
                  {role === "leader" && (
                    <td className="nowrap">
                      <button className="link" onClick={() => act(`/worksheets/${w.id}/${w.cancelled ? "restore" : "cancel"}`)}>
                        {w.cancelled ? "Restore" : "Cancel"}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {role === "leader" && (
            <p className="muted small">A cancelled worksheet is hidden from its researcher, and its lessons come back in their next one, which takes its number.</p>
          )}
        </section>
      )}
    </div>
  );
}

// A new worksheet for the researchers the leader picks: ask the Super Brain (saying what to cover,
// or leaving it to teach what's new from the others), or write the questions for it to finish.
// Either way one CREAO run writes one worksheet for each person picked.
function NewWorksheet({ session, project, onMade, onClose }) {
  const [people, setPeople] = useState(null); // [{ member_id, name, new_findings, since }]
  const [onlyNew, setOnlyNew] = useState(true);
  const [picked, setPicked] = useState(null); // member_id -> true
  const [mode, setMode] = useState("ask"); // "ask" | "write"
  const [request, setRequest] = useState("");
  const [title, setTitle] = useState("");
  const nextId = useRef(1);
  const blank = (kind = "question") => ({ id: nextId.current++, kind, prompt: "", model_answer: "" });
  const [questions, setQuestions] = useState(() => [blank()]);
  const [job, setJob] = useState(null); // { id, startedAt, researchers }
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");

  // In write mode the questions are the leader's, so any finding from the others may back them.
  const learnOnlyNew = mode === "ask" && onlyNew;
  useEffect(() => {
    backend(`/worksheets/next?project_id=${encodeURIComponent(project.id)}&only_new=${learnOnlyNew}`, { token: session.token })
      .then((next) => {
        setPeople(next.people);
        setPicked((current) => current || Object.fromEntries(next.people.map((p) => [p.member_id, p.new_findings > 0])));
      })
      .catch((e) => setError(e.message));
  }, [project.id, learnOnlyNew]);

  useEffect(() => {
    if (!job) return undefined;
    let stopped = false;
    let timer;
    const clock = setInterval(() => setElapsed(Math.floor((Date.now() - job.startedAt) / 1000)), 1000);
    async function poll() {
      try {
        const state = await backend(`/jobs/${job.id}`, { token: session.token });
        if (stopped) return;
        if (state.status === "running") {
          timer = setTimeout(poll, 750);
          return;
        }
        if (state.status === "failed") setError(state.error?.message || "CREAO couldn't write the worksheet. Try again.");
        else await onMade(state.diagnosis.worksheet_ids);
        setJob(null);
      } catch (e) {
        if (!stopped) { setError(e.message); setJob(null); }
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); clearInterval(clock); };
  }, [job]);

  const chosen = (people || []).filter((p) => picked?.[p.member_id]);
  const asked = request.trim().length > 0;
  // Asking with no request teaches what's new from the others, so anyone with nothing new is skipped.
  const skipped = mode === "ask" && !asked ? chosen.filter((p) => !p.new_findings) : [];
  const written = questions.filter((q) => q.prompt.trim());
  const ready = !job && chosen.length > skipped.length && (mode === "ask" || written.length > 0);
  const setQuestion = (id, change) => setQuestions(questions.map((q) => (q.id === id ? { ...q, ...change } : q)));

  async function send(event) {
    event.preventDefault();
    setError("");
    const body = { project_id: project.id, member_ids: chosen.map((p) => p.member_id), only_new: learnOnlyNew, request };
    if (mode === "write") {
      body.title = title;
      body.questions = written.map(({ kind, prompt, model_answer }) => ({ kind, prompt, model_answer }));
    }
    try {
      const started = await backend("/worksheets", { method: "POST", token: session.token, body });
      setElapsed(0);
      setJob({ id: started.job_id, startedAt: Date.now(), researchers: started.researchers });
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <form className="card new-worksheet" onSubmit={send}>
      <div className="card-header">
        <h3>New worksheet</h3>
        <button type="button" className="icon-btn" onClick={onClose} disabled={!!job} aria-label="Close" title="Close"><X /></button>
      </div>

      <div className="field-label">For</div>
      {!people && <p className="muted small">Loading the project's researchers…</p>}
      <ul className="pick-people" aria-label="Who gets this worksheet">
        {(people || []).map((p) => (
          <li key={p.member_id}>
            <label className="check">
              <input type="checkbox" disabled={!!job} checked={!!picked?.[p.member_id]}
                onChange={(e) => setPicked({ ...picked, [p.member_id]: e.target.checked })} />
              {p.name}
              <span className="muted small">
                {p.new_findings
                  ? `${p.new_findings} finding${p.new_findings === 1 ? "" : "s"} from others to learn from`
                  : learnOnlyNew && p.since ? "nothing new since their last worksheet" : "no findings from others yet"}
              </span>
            </label>
          </li>
        ))}
      </ul>

      <div className="sb-tabs" role="tablist" aria-label="How to make it">
        <button type="button" role="tab" aria-selected={mode === "ask"} className={mode === "ask" ? "active" : ""}
          onClick={() => setMode("ask")} disabled={!!job}><Sparkles /> Ask the Super Brain</button>
        <button type="button" role="tab" aria-selected={mode === "write"} className={mode === "write" ? "active" : ""}
          onClick={() => setMode("write")} disabled={!!job}>Write it myself</button>
      </div>

      {mode === "ask" ? (
        <>
          <label>
            <span className="label-row">What should it cover? <span className="muted small">Optional</span></span>
            <textarea rows={3} maxLength={2000} value={request} disabled={!!job} onChange={(e) => setRequest(e.target.value)}
              placeholder="e.g. The time step stability limit and why refining the grid brings the blow-up back. Two hands-on steps." />
          </label>
          <p className="muted small">Leave it empty to teach what the others found.</p>
          <label className="check">
            <input type="checkbox" checked={onlyNew} disabled={!!job} onChange={(e) => setOnlyNew(e.target.checked)} />
            Only findings that are new to each person since their last worksheet
          </label>
        </>
      ) : (
        <>
          <label>
            <span className="label-row">Title <span className="muted small">Optional</span></span>
            <input value={title} maxLength={120} disabled={!!job} onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Time step stability" />
          </label>
          <div className="field-label">Your questions</div>
          <ol className="question-editor">
            {questions.map((q, n) => (
              <li key={q.id} className="question-row">
                <div className="question-row-head">
                  <span className="question-number">{n + 1}</span>
                  <select value={q.kind} disabled={!!job} aria-label={`Kind of item ${n + 1}`}
                    onChange={(e) => setQuestion(q.id, { kind: e.target.value })}>
                    <option value="question">Question</option>
                    <option value="try">Try it</option>
                  </select>
                  {questions.length > 1 && (
                    <button type="button" className="icon-btn" disabled={!!job} aria-label={`Remove item ${n + 1}`} title="Remove"
                      onClick={() => setQuestions(questions.filter((x) => x.id !== q.id))}><X /></button>
                  )}
                </div>
                <textarea rows={2} maxLength={800} value={q.prompt} disabled={!!job} aria-label={`Item ${n + 1}`}
                  onChange={(e) => setQuestion(q.id, { prompt: e.target.value })}
                  placeholder={q.kind === "try"
                    ? "A small change to make in a copy of the project, what to predict, and what result to look for"
                    : "Your question"} />
                <input value={q.model_answer} maxLength={1500} disabled={!!job} aria-label={`Model answer for item ${n + 1}`}
                  onChange={(e) => setQuestion(q.id, { model_answer: e.target.value })}
                  placeholder="Model answer (optional: CREAO writes one when it's empty)" />
              </li>
            ))}
          </ol>
          <button type="button" className="link add-question" disabled={!!job || questions.length >= MAX_QUESTIONS}
            onClick={() => setQuestions([...questions, blank()])}><Plus /> Add a question</button>
          <label>
            <span className="label-row">Anything CREAO should know? <span className="muted small">Optional</span></span>
            <textarea rows={2} maxLength={2000} value={request} disabled={!!job} onChange={(e) => setRequest(e.target.value)}
              placeholder="e.g. Keep the wording simple; Researcher C is new to finite differences." />
          </label>
          <p className="muted small">CREAO keeps your questions in order, writes a model answer where you left none, groups them by topic and links the group's findings they rely on.</p>
        </>
      )}

      {skipped.length > 0 && (
        <p className="muted small">
          {skipped.map((p) => p.name).join(", ")} {skipped.length === 1 ? "has" : "have"} nothing new to learn from, so
          {skipped.length === 1 ? " is" : " are"} skipped. Say what to cover above to include them.
        </p>
      )}
      <div className="new-worksheet-actions">
        <button className="primary" type="submit" disabled={!ready}>
          {job ? "CREAO is writing…" : `Send to CREAO${chosen.length - skipped.length > 0 ? ` (${chosen.length - skipped.length})` : ""}`}
        </button>
        {job ? (
          <span role="status">CREAO is writing for {job.researchers.map((r) => r.name).join(", ")}… {elapsed}s</span>
        ) : (
          <span className="muted small">One CREAO run writes one worksheet for each person picked. Uses CREAO credits.</span>
        )}
      </div>
      <ErrorNote error={error} />
    </form>
  );
}

function Worksheet({ session, id, role, onOpenFinding, onBack }) {
  const [sheet, setSheet] = useState(null);
  const [answers, setAnswers] = useState({});
  const [comments, setComments] = useState({});
  const [overall, setOverall] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function show(s) {
    setSheet(s);
    setAnswers(Object.fromEntries(s.items.map((i) => [i.id, i.answer])));
    setComments(Object.fromEntries(s.items.map((i) => [i.id, i.leader_comment])));
    setOverall(s.leader_comment);
  }

  useEffect(() => {
    backend(`/worksheets/${id}`, { token: session.token }).then(show).catch((e) => setError(e.message));
  }, [id]);

  async function act(path, method, body, done) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await backend(`/worksheets/${id}${path}`, { method, token: session.token, body });
      if (result.items) show(result);
      setNotice(done);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm("Delete this worksheet and its answers?")) return;
    await act("", "DELETE", undefined, "");
    onBack();
  }

  if (!sheet) return <div><button className="link" onClick={onBack}>‹ Back to worksheets</button><ErrorNote error={error} /></div>;
  const answering = role === "researcher" && sheet.status !== "reviewed";
  const topics = [];
  for (const item of sheet.items) {
    const last = topics.at(-1);
    if (last && last.topic === item.topic) last.items.push(item);
    else topics.push({ topic: item.topic, items: [item] });
  }
  let number = 0;

  return (
    <div className="worksheet">
      <button className="link" onClick={onBack}>‹ Back to worksheets</button>
      <h2>{sheet.title}</h2>
      <p className="muted">
        Worksheet {sheet.number} · For {sheet.member_name} ·{" "}
        <span className={`tag worksheet-${sheet.status}`}>{STATUS[role][sheet.status]}</span> · made{" "}
        {sheet.created_at.slice(0, 10)}
        {role === "researcher" && sheet.status !== "reviewed" && " · The model answers appear once your leader has checked your answers."}
      </p>
      {sheet.cancelled && (
        <p className="warning-note">
          This worksheet is cancelled, so {sheet.member_name} can't see it. Restore it from the list of worksheets.
        </p>
      )}
      {sheet.status === "reviewed" && sheet.leader_comment && (
        <p className="leader-comment"><span className="label">Leader</span>{sheet.leader_comment}</p>
      )}

      {topics.map((group, g) => (
        <section className="card" key={g}>
          <h3>{group.topic}</h3>
          {group.items.map((item) => {
            number += 1;
            return (
              <div className={`worksheet-item item-${item.kind}`} key={item.id}>
                <p className="item-prompt">
                  <span className="label">{item.kind === "try" ? `${number} · Try it` : `${number} · Question`}</span>
                  {item.prompt}
                </p>
                {item.findings.length > 0 && (
                  <p className="small muted">
                    From:{" "}
                    {item.findings.map((f, n) => (
                      <span key={f.id}>
                        {n > 0 && " · "}
                        <button className="link" onClick={() => onOpenFinding(f.id)}>{f.title}</button>
                      </span>
                    ))}
                  </p>
                )}
                {item.kind === "try" && answering && (
                  <p className="small muted">Try it as a new version under Simulations → Set up &amp; run: it runs in a saved copy, your real project isn't changed, and it only runs when you click Run.</p>
                )}
                {answering ? (
                  <textarea rows={3} maxLength={4000} value={answers[item.id] || ""} disabled={busy}
                    aria-label={`Your answer to ${number}`}
                    placeholder={item.kind === "try" ? "What you predicted, what you ran, and what happened" : "Your answer"}
                    onChange={(e) => setAnswers({ ...answers, [item.id]: e.target.value })} />
                ) : (
                  <p className="item-answer"><span className="label">{role === "leader" ? `${sheet.member_name}'s answer` : "Your answer"}</span>
                    {item.answer || <span className="muted">No answer yet.</span>}</p>
                )}
                {item.model_answer && (
                  <p className="item-model"><span className="label">Model answer</span>{item.model_answer}</p>
                )}
                {role === "leader" ? (
                  <textarea rows={2} maxLength={2000} value={comments[item.id] || ""} disabled={busy}
                    aria-label={`Your comment on ${number}`} placeholder="Your comment (optional)"
                    onChange={(e) => setComments({ ...comments, [item.id]: e.target.value })} />
                ) : (
                  item.leader_comment && <p className="leader-comment"><span className="label">Leader</span>{item.leader_comment}</p>
                )}
              </div>
            );
          })}
        </section>
      ))}

      <section className="card worksheet-actions">
        {role === "researcher" && answering && (
          <>
            <button disabled={busy} onClick={() => act("/answers", "PUT", { answers }, "Answers saved.")}>Save</button>
            <button className="primary" disabled={busy}
              onClick={() => act("/answers", "PUT", { answers, submit: true }, "Sent to your leader. You can still change your answers until they check them.")}>
              Send to the leader
            </button>
          </>
        )}
        {role === "leader" && (
          <>
            <textarea rows={2} maxLength={2000} value={overall} disabled={busy} aria-label="Comment on the whole worksheet"
              placeholder="A comment on the whole worksheet (optional)" onChange={(e) => setOverall(e.target.value)} />
            <div className="row-buttons">
              <button className="primary" disabled={busy}
                onClick={() => act("/review", "POST", { comments, comment: overall }, `Comments sent to ${sheet.member_name}.`)}>
                {sheet.status === "reviewed" ? "Update comments" : "Send comments"}
              </button>
              <button disabled={busy} onClick={remove}>Delete</button>
            </div>
          </>
        )}
        {notice && <p className="muted" role="status">{notice}</p>}
        <ErrorNote error={error} />
      </section>
    </div>
  );
}
