import { useEffect, useRef, useState } from "react";
import { backend } from "../api.js";
import { ErrorNote } from "../components.jsx";

const LINKED = new URLSearchParams(window.location.search).get("worksheet");

const STATUS = {
  leader: { assigned: "Waiting for answers", submitted: "Ready to check", reviewed: "Checked" },
  researcher: { assigned: "To do", submitted: "Sent to the leader", reviewed: "Checked by the leader" },
};

// The Super Brain's teacher: the leader makes a worksheet per researcher from what the
// others found; researchers answer; the leader reads the answers and comments.
export default function WorksheetsView({ session, project, onOpenFinding }) {
  const [list, setList] = useState(null);
  const [openId, setOpenId] = useState(LINKED); // ?worksheet=<id> opens one
  const [error, setError] = useState("");
  const shownProject = useRef(project.id);
  const role = session.member.role === "leader" ? "leader" : "researcher";

  function load() {
    return backend(`/worksheets?project_id=${encodeURIComponent(project.id)}`, { token: session.token })
      .then((rows) => {
        setList(rows);
        setError("");
      })
      .catch((e) => setError(e.message));
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
  return (
    <div>
      <h2>Worksheets</h2>
      <p className="muted">
        {role === "leader"
          ? `The Super Brain as a teacher: each researcher in ${project.name} gets questions and things to try, made from what the others shared. You read their answers and comment.`
          : `Questions and things to try, made by the Super Brain from what others in ${project.name} shared. Your leader reads your answers and comments.`}
      </p>
      <ErrorNote error={error} />
      {role === "leader" && <MakeWorksheets session={session} project={project} onMade={load} />}
      {list && !list.length && (
        <section className="card">
          <p className="muted">{role === "leader" ? "No worksheets yet." : "No worksheets for you yet."}</p>
        </section>
      )}
      {list && list.length > 0 && (
        <section className="card">
          <table className="worksheet-table">
            <thead>
              <tr>
                {role === "leader" && <th>Researcher</th>}
                <th>Worksheet</th>
                <th>Status</th>
                <th>Answered</th>
                <th>Made</th>
              </tr>
            </thead>
            <tbody>
              {list.map((w) => (
                <tr key={w.id}>
                  {role === "leader" && <td className="nowrap">{w.member_name}</td>}
                  <td>
                    <button className="link" onClick={() => setOpenId(w.id)}>{w.title}</button>
                  </td>
                  <td className="nowrap"><span className={`tag worksheet-${w.status}`}>{STATUS[role][w.status]}</span></td>
                  <td className="nowrap">{w.answered} of {w.items}</td>
                  <td className="nowrap muted">{w.created_at.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function MakeWorksheets({ session, project, onMade }) {
  const [job, setJob] = useState(null); // { id, startedAt, researchers }
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

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
          timer = setTimeout(poll, 1500);
          return;
        }
        if (state.status === "failed") setError(state.error?.message || "The Super Brain couldn't write the worksheets. Try again.");
        else {
          const made = state.diagnosis.worksheet_ids.length;
          setNotice(made ? `${made} worksheet${made === 1 ? "" : "s"} made.` : "The Super Brain didn't return usable worksheets. Try again.");
          onMade();
        }
        setJob(null);
      } catch (e) {
        if (!stopped) { setError(e.message); setJob(null); }
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); clearInterval(clock); };
  }, [job]);

  async function start() {
    setError("");
    setNotice("");
    try {
      const started = await backend("/worksheets", { method: "POST", token: session.token, body: { project_id: project.id } });
      setElapsed(0);
      setJob({ id: started.job_id, startedAt: Date.now(), researchers: started.researchers, findings: started.findings });
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <section className="card teacher">
      <div className="card-header">
        <h3>Make worksheets</h3>
        <button className="primary" onClick={start} disabled={!!job}>{job ? "Writing…" : "Make worksheets"}</button>
      </div>
      {job ? (
        <p role="status">
          The Super Brain is reading {job.findings} shared finding{job.findings === 1 ? "" : "s"} and writing a worksheet for{" "}
          {job.researchers.map((r) => r.name).join(", ")}… {elapsed}s
        </p>
      ) : (
        <p className="muted">
          One Super Brain run reads every finding shared in {project.name}, sorts them into topics, and writes each researcher
          questions and things to try from what the others found. Uses CREAO credits.
        </p>
      )}
      {notice && <p className="muted" role="status">{notice}</p>}
      <ErrorNote error={error} />
    </section>
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
        For {sheet.member_name} · <span className={`tag worksheet-${sheet.status}`}>{STATUS[role][sheet.status]}</span> · made{" "}
        {sheet.created_at.slice(0, 10)}
        {role === "researcher" && sheet.status !== "reviewed" && " · The model answers appear once your leader has checked your answers."}
      </p>
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
                  <p className="small muted">Try it in a copy on the Experiments page: your real project isn't changed, and it only runs when you click Run.</p>
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
