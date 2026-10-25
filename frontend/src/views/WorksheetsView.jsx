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
  const [cancels, setCancels] = useState(0); // a cancel or restore changes next week's number and lessons
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
  async function act(path) {
    try {
      await backend(path, { method: "POST", token: session.token });
      await load();
      setCancels((n) => n + 1);
    } catch (e) {
      setError(e.message);
    }
  }

  const weeks = byWeek(list || []);
  return (
    <div>
      <h2>Worksheets</h2>
      <p className="muted">
        {role === "leader"
          ? `Each week, the Super Brain teaches each researcher in ${project.name} what's new from the others: questions and things to try. You choose who gets one, read their answers and comment. A week, or one person's worksheet, can be cancelled; the next week made takes a cancelled week's number.`
          : `Each week's questions and things to try, made by the Super Brain from what others in ${project.name} shared. Your leader reads your answers and comments.`}
      </p>
      <ErrorNote error={error} />
      {role === "leader" && <MakeWorksheets session={session} project={project} cancels={cancels} onMade={load} />}
      {list && !list.length && (
        <section className="card">
          <p className="muted">{role === "leader" ? "No worksheets yet." : "No worksheets for you yet."}</p>
        </section>
      )}
      {weeks.map((week) => (
        <section className={`card worksheet-week ${week.cancelled ? "cancelled" : ""}`} key={week.id}>
          <div className="card-header">
            <h3>
              Week {week.week}
              {week.cancelled && <span className="tag worksheet-cancelled">Cancelled</span>}
            </h3>
            <span className="muted small">
              made {week.made} · {week.onlyNew ? "what was new to each person" : "everything shared"}
            </span>
            {role === "leader" && (
              <button onClick={() => act(`/worksheets/weeks/${week.id}/${week.cancelled ? "restore" : "cancel"}`)}>
                {week.cancelled ? "Restore week" : "Cancel week"}
              </button>
            )}
          </div>
          <table className="worksheet-table">
            <thead>
              <tr>
                {role === "leader" && <th>Researcher</th>}
                <th>Worksheet</th>
                <th>Status</th>
                <th>Answered</th>
                {role === "leader" && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {week.sheets.map((w) => (
                <tr key={w.id} className={w.cancelled === "person" ? "cancelled" : ""}>
                  {role === "leader" && <td className="nowrap">{w.member_name}</td>}
                  <td>
                    <button className="link" onClick={() => setOpenId(w.id)}>{w.title}</button>
                  </td>
                  <td className="nowrap">
                    {w.cancelled === "person"
                      ? <span className="tag worksheet-cancelled">Cancelled</span>
                      : <span className={`tag worksheet-${w.status}`}>{STATUS[role][w.status]}</span>}
                  </td>
                  <td className="nowrap">{w.answered} of {w.items}</td>
                  {role === "leader" && (
                    <td className="nowrap">
                      {!week.cancelled && (
                        <button className="link" onClick={() => act(`/worksheets/${w.id}/${w.cancelled ? "restore" : "cancel"}`)}>
                          {w.cancelled ? "Restore" : "Cancel"}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

// Worksheets grouped by their week, newest week first.
function byWeek(list) {
  const weeks = new Map();
  for (const w of list) {
    const key = w.week_id || w.id;
    if (!weeks.has(key)) {
      weeks.set(key, { id: w.week_id, week: w.week, made: w.created_at.slice(0, 10), onlyNew: w.only_new,
        cancelled: w.cancelled === "week", sheets: [] });
    }
    weeks.get(key).sheets.push(w);
  }
  return [...weeks.values()].sort((a, b) => b.week - a.week);
}

// Next week's worksheets: who gets one, and only what's new to them (or everything).
function MakeWorksheets({ session, project, cancels, onMade }) {
  const [plan, setPlan] = useState(null); // { week, people: [{ member_id, name, new_findings, since }] }
  const [onlyNew, setOnlyNew] = useState(true);
  const [picked, setPicked] = useState({}); // member_id -> true
  const [job, setJob] = useState(null); // { id, startedAt, researchers, week }
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function loadPlan() {
    return backend(`/worksheets/next?project_id=${encodeURIComponent(project.id)}&only_new=${onlyNew}`, { token: session.token })
      .then((next) => {
        setPlan(next);
        setPicked(Object.fromEntries(next.people.map((p) => [p.member_id, p.new_findings > 0])));
      })
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    loadPlan();
  }, [project.id, onlyNew, cancels]);

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
          setNotice(made
            ? `Week ${state.diagnosis.week}: ${made} worksheet${made === 1 ? "" : "s"} made.`
            : "The Super Brain didn't return usable worksheets. Try again.");
          onMade();
          loadPlan();
        }
        setJob(null);
      } catch (e) {
        if (!stopped) { setError(e.message); setJob(null); }
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); clearInterval(clock); };
  }, [job]);

  const chosen = (plan?.people || []).filter((p) => picked[p.member_id] && p.new_findings > 0);

  async function start() {
    setError("");
    setNotice("");
    try {
      const started = await backend("/worksheets", {
        method: "POST",
        token: session.token,
        body: { project_id: project.id, only_new: onlyNew, member_ids: chosen.map((p) => p.member_id) },
      });
      setElapsed(0);
      setJob({ id: started.job_id, startedAt: Date.now(), researchers: started.researchers, week: started.week });
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <section className="card teacher">
      <div className="card-header">
        <h3>{plan ? `Week ${plan.week}` : "Next week"}</h3>
        <button className="primary" onClick={start} disabled={!!job || !chosen.length}>
          {job ? "Writing…" : `Make week ${plan?.week ?? ""} worksheets${chosen.length ? ` (${chosen.length})` : ""}`}
        </button>
      </div>
      <label className="check">
        <input type="checkbox" checked={onlyNew} disabled={!!job} onChange={(e) => setOnlyNew(e.target.checked)} />
        Only what's new to each person since their last worksheet
      </label>
      <ul className="pick-people" aria-label="Who gets a worksheet this week">
        {(plan?.people || []).map((p) => (
          <li key={p.member_id}>
            <label className={`check ${p.new_findings ? "" : "muted"}`}>
              <input type="checkbox" disabled={!!job || !p.new_findings} checked={!!picked[p.member_id] && p.new_findings > 0}
                onChange={(e) => setPicked({ ...picked, [p.member_id]: e.target.checked })} />
              {p.name}
              <span className="muted small">
                {p.new_findings
                  ? `${p.new_findings} finding${p.new_findings === 1 ? "" : "s"} to learn from`
                  : onlyNew && p.since ? "nothing new since their last worksheet" : "nothing to learn from yet"}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {job ? (
        <p role="status">
          The Super Brain is writing week {job.week} for {job.researchers.map((r) => r.name).join(", ")}… {elapsed}s
        </p>
      ) : (
        <p className="muted small">
          One Super Brain run writes them all. It builds on what each person covered in earlier weeks. Uses CREAO credits.
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
        {sheet.week && `Week ${sheet.week} · `}For {sheet.member_name} ·{" "}
        <span className={`tag worksheet-${sheet.status}`}>{STATUS[role][sheet.status]}</span> · made{" "}
        {sheet.created_at.slice(0, 10)}
        {role === "researcher" && sheet.status !== "reviewed" && " · The model answers appear once your leader has checked your answers."}
      </p>
      {sheet.cancelled && (
        <p className="muted">
          {sheet.cancelled === "week" ? `Week ${sheet.week} is cancelled` : "This worksheet is cancelled"}, so{" "}
          {sheet.member_name} can't see it. Restore it from the list of worksheets.
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
