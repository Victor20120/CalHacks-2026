import { useEffect, useId, useMemo, useRef, useState } from "react";
import { BookOpen, FileCode2, GraduationCap, Plus, Sparkles, Trash2, X } from "lucide-react";
import { backend, CONNECTOR_HELP, connector } from "../api.js";
import { formatValue } from "../charts.jsx";
import { ErrorNote, FolderPicker, Markdown } from "../components.jsx";

// Lessons: a researcher gives the Super Brain a paper and the code file built from it (read whole),
// and it writes the lesson: the idea in plain words, the paper's equations drawn, every term explained
// with the variable that holds it, where the code does it, and pictures (a flow of the steps, a
// stencil grid, plots ExpandRG computes). Papers are checked on Crossref. Private to the researcher.
// KaTeX draws the equations; it loads only on this tab.

const SOURCE_STATUS = {
  provided: ["You gave this paper", "project"],
  project: ["Cited in the project", "project"],
  crossref: ["Found on Crossref", "found"],
  doi_mismatch: ["DOI is another paper's", "doubt"],
  not_found: ["Not found: may be wrong", "doubt"],
  unchecked: ["Not checked", "unchecked"],
};

let katexLoading = null;
function useKatex() {
  const [katex, setKatex] = useState(null);
  useEffect(() => {
    katexLoading ||= Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([m]) => m.default || m);
    katexLoading.then(setKatex).catch(() => setKatex(false));
  }, []);
  return katex;
}

export default function LessonsView({ session, project, onOpenFinding }) {
  const [list, setList] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [composing, setComposing] = useState(null); // null, or what to start from: { folder, path, paper }
  const [error, setError] = useState("");

  function load() {
    return backend(`/lessons?project_id=${encodeURIComponent(project.id)}`, { token: session.token })
      .then((rows) => { setList(rows); setError(""); if (!rows.length) setComposing((c) => c || {}); })
      .catch((e) => setError(e.message));
  }

  useEffect(() => { setOpenId(null); setComposing(null); load(); }, [project.id]);

  if (openId) {
    return <Lesson session={session} id={openId} onOpenFinding={onOpenFinding}
      onBack={() => { setOpenId(null); load(); }}
      onAskMore={(start) => { setOpenId(null); setComposing(start); }}
      onDeleted={() => { setOpenId(null); load(); }} />;
  }

  return (
    <div>
      <div className="worksheets-head">
        <div>
          <h2>Lessons</h2>
          <p className="muted">
            Give the Super Brain a paper and the code built from it. It reads both and writes you a lesson: the paper's idea
            in plain words, its equations and where each one is in the code, and pictures that make it clear. Only you can
            see your lessons.
          </p>
        </div>
        {!composing && <button className="primary" onClick={() => setComposing({})}><Plus /> New lesson</button>}
      </div>
      <ErrorNote error={error} />
      {composing && (
        <NewLesson session={session} project={project} start={composing} onClose={list?.length ? () => setComposing(null) : null}
          onMade={(id) => { setComposing(null); load(); setOpenId(id); }} />
      )}
      {list?.length > 0 && (
        <section className="card">
          <h3>Your lessons</h3>
          <table className="worksheet-table">
            <thead><tr><th>Lesson</th><th>Paper</th><th>Code</th><th>Made</th></tr></thead>
            <tbody>
              {list.map((l) => (
                <tr key={l.id}>
                  <td>
                    <button className="link worksheet-link" onClick={() => setOpenId(l.id)}>
                      <strong>{l.title || "Lesson"}</strong>{l.question && <span className="muted"> · {l.question}</span>}
                    </button>
                  </td>
                  <td className="mono small">{l.paper || <span className="muted">–</span>}</td>
                  <td className="mono small nowrap">{l.path}:{l.lines}</td>
                  <td className="muted small nowrap">{l.created_at.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

// Give the paper and the code that implements it, both from granted folders; a focus is optional.
// The whole code file is read. What will be sent loads by itself, then the Super Brain writes the lesson.
function NewLesson({ session, project, start, onMade, onClose }) {
  const [root, setRoot] = useState("");
  const [grants, setGrants] = useState(null);
  const [under, setUnder] = useState("");
  const [listing, setListing] = useState(null);
  const [path, setPath] = useState(start.path || "");
  const [paperFolder, setPaperFolder] = useState(start.paper?.folder || "");
  const [otherFolder, setOtherFolder] = useState(false); // the paper's folder picker, shown on request
  const [papers, setPapers] = useState(null);
  const [paperPath, setPaperPath] = useState(start.paper?.path || "");
  const [paperPages, setPaperPages] = useState(start.paper?.pages && start.paper.kind === "pdf" ? start.paper.pages : "");
  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState(null); // { key, excerpt, references, paper }
  const [pending, setPending] = useState(null); // { jobId, startedAt }
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const shownFolder = useRef("");
  const folder = grants?.find((g) => g.root === root)?.name || "";
  const paperIn = paperFolder || folder;
  const isPdf = paperPath.toLowerCase().endsWith(".pdf");
  const pages = isPdf && paperPages.trim() ? paperPages.trim().replace(/\s+/g, "") : null;
  const key = `${folder}|${path}|${paperIn}|${paperPath}|${pages}`;

  // The folders granted on this computer; "Another lesson on this" starts in the lesson's folder.
  // Read again when a folder is added through the picker.
  useEffect(() => {
    if (grants && (!root || grants.some((g) => g.root === root))) return;
    connector("/grants")
      .then((list) => {
        setGrants(list);
        const own = !root && list.find((g) => g.name === start.folder);
        if (own) setRoot(own.root);
      })
      .catch(() => { setGrants([]); setError(CONNECTOR_HELP); });
  }, [root]);
  useEffect(() => { // another folder: start again in it
    if (shownFolder.current && shownFolder.current !== folder) { setPath(""); setUnder(""); setPreview(null); }
    shownFolder.current = folder;
  }, [folder]);
  function loadFiles() {
    if (!folder) return;
    connector("/tools/list_project_files", { method: "POST", body: { folder, under } })
      .then((l) => { setListing(l); setError(""); })
      .catch((e) => setError(e.message));
  }
  useEffect(loadFiles, [folder, under]);
  // The papers in the paper's folder (the code's, unless another is picked). Both lists are read
  // again whenever they're opened, so a file saved while this page was open shows up.
  function loadPapers() {
    if (!paperIn) return;
    connector("/tools/list_papers", { method: "POST", body: { folder: paperIn } })
      .then((l) => { setPapers(l.files); if (paperPath && !l.files.includes(paperPath)) setPaperPath(""); })
      .catch((e) => setError(e.message));
  }
  useEffect(loadPapers, [paperIn]);

  useEffect(() => {
    if (!pending) return undefined;
    let stopped = false;
    let timer;
    const clock = setInterval(() => setElapsed(Math.floor((Date.now() - pending.startedAt) / 1000)), 1000);
    async function poll() {
      try {
        const job = await backend(`/jobs/${pending.jobId}`, { token: session.token });
        if (stopped) return;
        if (job.status === "running") { timer = setTimeout(poll, 750); return; }
        setPending(null);
        if (job.status === "failed") setError(job.error?.message || "The Super Brain couldn't write the lesson. Try again.");
        else onMade(job.diagnosis.lesson_id);
      } catch (e) {
        if (!stopped) { setError(e.message); setPending(null); }
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); clearInterval(clock); };
  }, [pending]);

  async function look() {
    setError("");
    try {
      const [excerpt, refs, paper] = await Promise.all([
        connector("/tools/read_code", { method: "POST", body: { folder, path } }),
        connector("/tools/project_references", { method: "POST", body: { folder, path } }),
        paperPath ? connector("/tools/read_paper", { method: "POST", body: { folder: paperIn, path: paperPath, pages } }) : null,
      ]);
      setPreview({ key, excerpt, references: refs.references, paper });
    } catch (e) {
      setPreview(null);
      setError(e.message);
    }
  }

  // What will be sent loads by itself once a code file is picked, and again after any change, so
  // Write the lesson is always about what's shown.
  useEffect(() => {
    if (!folder || !path || pending) return undefined;
    const timer = setTimeout(look, 500);
    return () => clearTimeout(timer);
  }, [key]);

  async function send(event) {
    event.preventDefault();
    setError("");
    try {
      const started = await backend("/lessons", { method: "POST", token: session.token, body: {
        project_id: project.id, folder, path, question: question.trim(),
        ...(paperPath ? { paper_folder: paperIn, paper_path: paperPath, paper_pages: pages } : {}),
      } });
      setElapsed(0);
      setPending({ jobId: started.job_id, startedAt: Date.now() });
    } catch (e) {
      setError(e.message);
    }
  }

  const fresh = preview?.key === key;
  const sentPaper = fresh ? preview.paper : null;
  const sentCode = fresh ? preview.excerpt : null;
  const waiting = pending ? "" : !folder ? "Pick the project folder first." : !path ? "Pick the code file." : error ? ""
    : !fresh ? "Reading the paper and the code…" : "";
  const codeFiles = (listing?.files || []).filter(isCode);
  const otherFiles = (listing?.files || []).filter((f) => !isCode(f));
  return (
    <section className="card new-lesson">
      <div className="card-header">
        <h3><GraduationCap /> New lesson</h3>
        {onClose && <button className="icon-btn" onClick={onClose} aria-label="Close" disabled={!!pending}><X /></button>}
      </div>
      <form onSubmit={send}>
        {grants && <FolderPicker root={root} onChange={setRoot} />}

        <div className="lesson-inputs">
          <div className={`input-card ${paperPath ? "chosen" : ""}`}>
            <div className="input-card-head">
              <span className="input-icon"><BookOpen /></span>
              <span><strong>The paper</strong><span className="muted xs">The paper the code is based on: a PDF, LaTeX or text file</span></span>
            </div>
            {grants?.length > 1 && (otherFolder || paperFolder) && (
              <label className="input-sub">From folder
                <select value={paperIn} onChange={(e) => { setPaperFolder(e.target.value); setPaperPath(""); }}>
                  {grants.map((g) => <option key={g.root} value={g.name}>{g.name}</option>)}
                </select>
              </label>
            )}
            <select className="input-select" value={paperPath} onChange={(e) => setPaperPath(e.target.value)} onFocus={loadPapers}
              onMouseDown={loadPapers} aria-label="The paper" disabled={!!pending}>
              <option value="">No paper: explain the code alone</option>
              {(papers || []).some(isPaper) && (
                <optgroup label="Papers (PDF, LaTeX)">
                  {papers.filter(isPaper).map((f) => <option key={f} value={f}>{f}</option>)}
                </optgroup>
              )}
              {(papers || []).some((f) => !isPaper(f)) && (
                <optgroup label="Notes and text files">
                  {papers.filter((f) => !isPaper(f)).map((f) => <option key={f} value={f}>{f}</option>)}
                </optgroup>
              )}
            </select>
            {isPdf && (
              <label className="input-sub">Pages
                <input value={paperPages} onChange={(e) => setPaperPages(e.target.value)} placeholder="all" maxLength={11} disabled={!!pending} />
              </label>
            )}
            <p className="input-meta">
              {sentPaper ? (sentPaper.kind === "pdf"
                ? `${sentPaper.total_pages} pages${sentPaper.truncated ? `: pages ${sentPaper.pages} are read (type the pages with the method to pick others)` : ", all read"}`
                : `${sentPaper.text.length.toLocaleString()} characters${sentPaper.truncated ? ", the start is read" : ", all read"}`)
                : paperPath ? "Reading…" : "Not in the list? Save it into the project folder, then open the list again."}
            </p>
            {grants?.length > 1 && !otherFolder && !paperFolder && (
              <button type="button" className="link input-other" onClick={() => setOtherFolder(true)}>Paper in another folder?</button>
            )}
          </div>

          <div className={`input-card ${path ? "chosen" : ""}`}>
            <div className="input-card-head">
              <span className="input-icon"><FileCode2 /></span>
              <span><strong>The code</strong><span className="muted xs">The file that implements it. The whole file is read.</span></span>
            </div>
            {listing?.folders && (
              <label className="input-sub">Subfolder
                <select value={under} onChange={(e) => { setUnder(e.target.value); setPath(""); }} disabled={!!pending}>
                  <option value="">Top of {listing.folder}</option>
                  {listing.folders.map((f) => <option key={f.path} value={f.path}>{f.path} ({f.research_files})</option>)}
                </select>
              </label>
            )}
            <select className="input-select" value={path} onChange={(e) => setPath(e.target.value)} onFocus={loadFiles}
              aria-label="The code" disabled={!!pending || !listing}>
              <option value="">Pick a file…</option>
              {codeFiles.length > 0 && <optgroup label="Code">{codeFiles.map((f) => <option key={f} value={f}>{f}</option>)}</optgroup>}
              {otherFiles.length > 0 && <optgroup label="Other files">{otherFiles.map((f) => <option key={f} value={f}>{f}</option>)}</optgroup>}
            </select>
            <p className="input-meta">
              {sentCode ? `${sentCode.total_lines.toLocaleString()} lines${sentCode.truncated
                ? `: the first ${sentCode.lines.split("-")[1]} are read (the file is too long to send whole)` : ", all read"}`
                : path ? "Reading…" : ""}
            </p>
          </div>
        </div>

        <label className="lesson-focus">
          <span>Anything to focus on? <span className="muted">(optional)</span></span>
          <input value={question} maxLength={2000} onChange={(e) => setQuestion(e.target.value)} disabled={!!pending}
            placeholder="e.g. how the boundary is handled" />
        </label>

        <div className="lesson-actions">
          <button className="primary" type="submit" disabled={!fresh || !!pending}>
            <Sparkles /> {pending ? `The Super Brain is writing your lesson… ${elapsed}s` : "Write the lesson"}
          </button>
          {waiting && <span className="muted small">{waiting}</span>}
        </div>
        <ErrorNote error={error} />

        {fresh && (
          <details className="lesson-sent">
            <summary>See exactly what's sent</summary>
            <p className="small">
              To the Super Brain (CREAO): {preview.paper ? `the paper ${preview.paper.path}` : "no paper"}, {preview.excerpt.path} ({preview.excerpt.lines === `1-${preview.excerpt.total_lines}`
                ? "the whole file" : `lines ${preview.excerpt.lines} of ${preview.excerpt.total_lines}`}){question.trim() ? ", your focus" : ""}
              {preview.references.length ? `, ${preview.references.length} paper${preview.references.length > 1 ? "s" : ""} the project cites` : ""}
              {(preview.excerpt.redactions || 0) + (preview.paper?.redactions || 0) ? ` (secrets blanked)` : ""}, and up to 3 of the group's
              findings that match. Crossref gets only paper titles and DOIs.
            </p>
            {preview.paper && <pre className="code-block">{preview.paper.text.slice(0, 4000)}{preview.paper.text.length > 4000 ? "\n…" : ""}</pre>}
            <pre className="code-block">{preview.excerpt.text}</pre>
          </details>
        )}
      </form>
    </section>
  );
}

const isPaper = (f) => /\.(pdf|tex)$/i.test(f);
const isCode = (f) => /\.(f|for|ftn|f77|f90|f95|f03|f08|py|pyx|ipynb|m|jl|r|c|h|cc|cpp|cxx|hpp|cu|cuh|rs|go|java|scala|sh|bash)$/i.test(f);

// One lesson, beside the code it explains. Hovering or clicking any "lines" chip highlights them.
function Lesson({ session, id, onBack, onOpenFinding, onAskMore, onDeleted }) {
  const [data, setData] = useState(null);
  const [lit, setLit] = useState(null); // [start, end] highlighted in the code
  const [error, setError] = useState("");
  const katex = useKatex();

  useEffect(() => {
    backend(`/lessons/${id}`, { token: session.token }).then(setData).catch((e) => setError(e.message));
  }, [id]);

  async function remove() {
    if (!window.confirm("Delete this lesson? Your code isn't touched.")) return;
    try {
      await backend(`/lessons/${id}`, { method: "DELETE", token: session.token });
      onDeleted();
    } catch (e) {
      setError(e.message);
    }
  }

  if (!data) return <div><button className="link" onClick={onBack}>← All lessons</button><ErrorNote error={error} />{!error && <p className="muted">Loading…</p>}</div>;
  const { lesson } = data;
  const show = (lines) => setLit(lines || null);

  return (
    <div className="lesson">
      <button className="link" onClick={onBack}>← All lessons</button>
      <div className="lesson-head">
        <div>
          <h2>{lesson.title}</h2>
          <p className="muted small">
            {data.paper ? <>Paper <span className="mono">{data.paper.path}</span>{data.paper.pages ? ` (pages ${data.paper.pages})` : ""} · </> : "No paper · "}
            code <span className="mono">{data.path}:{data.lines}</span> · {data.created_at.slice(0, 10)}
            {data.question ? <> · focus: “{data.question}”</> : null}
          </p>
        </div>
        <div className="row-buttons">
          <button onClick={() => onAskMore({ folder: data.folder, path: data.path, paper: data.paper })}>Another lesson on this</button>
          <button className="link" onClick={remove}><Trash2 /> Delete</button>
        </div>
      </div>
      <ErrorNote error={error} />

      <div className="lesson-layout">
        <div className="lesson-body">
          {lesson.summary && <section className="card"><Markdown text={lesson.summary} /></section>}

          {(lesson.pictures?.length > 0 || lesson.picture_notes?.length > 0) && (
            <section className="card">
              <h3>See it</h3>
              <div className="pictures">
                {lesson.pictures.map((p, i) => (
                  <figure key={i} className={`picture picture-${p.type}`}>
                    {p.title && <figcaption className="picture-title">{p.title}</figcaption>}
                    {p.type === "flow" && <FlowPicture picture={p} onShow={show} />}
                    {p.type === "grid" && <GridPicture picture={p} onShow={show} />}
                    {p.type === "plot" && <PlotPicture picture={p} />}
                    {p.caption && <p className="small picture-caption">{p.caption}</p>}
                  </figure>
                ))}
              </div>
              {lesson.picture_notes?.map((n) => <p key={n} className="muted xs">{n}</p>)}
            </section>
          )}

          {lesson.sources.length > 0 && (
            <section className="card">
              <h3>Where it comes from</h3>
              <ol className="lesson-sources">
                {lesson.sources.map((s, i) => <Source key={i} source={s} references={lesson.references || []} />)}
              </ol>
            </section>
          )}

          {lesson.equations.length > 0 && (
            <section className="card">
              <h3>The equations</h3>
              {lesson.equations.map((e, i) => (
                <article key={i} className="equation">
                  <div className="equation-head">
                    <strong>{e.name || `Equation ${i + 1}`}</strong>
                    {e.paper_ref && <span className="tag">{e.paper_ref}</span>}
                    {e.sources.length > 0 && <span className="muted xs">from {e.sources.map((n) => `[${n + 1}]`).join(" ")}</span>}
                    <Lines value={e} onShow={show} />
                  </div>
                  {e.latex ? <TeX katex={katex} latex={e.latex} display /> : null}
                  {e.latex_note && <p className="muted xs">{e.latex_note}</p>}
                  {e.plain && <p className="small"><span className="muted">In plain text:</span> <code>{e.plain}</code></p>}
                  {e.meaning && <Markdown text={e.meaning} />}
                  {e.terms.length > 0 && (
                    <div className="table-scroll">
                      <table className="numbers-table terms-table">
                        <thead><tr><th>Term</th><th>Meaning</th><th>Units</th><th>In the code</th><th>Lines</th></tr></thead>
                        <tbody>
                          {e.terms.map((t, j) => (
                            <tr key={j} onMouseEnter={() => t.lines && show(t.lines)}>
                              <td>{t.symbol ? <TeX katex={katex} latex={t.symbol} /> : "–"}</td>
                              <td>{t.meaning}</td>
                              <td className="nowrap">{t.units || <span className="muted">–</span>}</td>
                              <td>
                                {t.code.length ? t.code.map((c) => (
                                  <code key={c.name} className={c.found ? "" : "missing"} title={c.found ? "" : "Not in the code that was sent"}>{c.name}</code>
                                )) : <span className="muted">–</span>}
                              </td>
                              <td><Lines value={t} onShow={show} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {e.in_code && <div className="in-code"><span className="field-label">How the code does it</span><Markdown text={e.in_code} /></div>}
                </article>
              ))}
              {lesson.equations.some((e) => e.terms.some((t) => t.code.some((c) => !c.found))) && (
                <p className="muted xs">A name <code className="missing">struck through</code> isn't in the code that was sent: the Super Brain may be wrong about it.</p>
              )}
            </section>
          )}

          {lesson.walkthrough.length > 0 && (
            <section className="card">
              <h3>Through the code</h3>
              <ol className="walkthrough">
                {lesson.walkthrough.map((w, i) => (
                  <li key={i} onMouseEnter={() => w.lines && show(w.lines)}><Lines value={w} onShow={show} /> <Markdown text={w.text} /></li>
                ))}
              </ol>
            </section>
          )}

          {lesson.caveats.length > 0 && (
            <section className="card">
              <h3>Watch out for</h3>
              <ul className="lesson-list">{lesson.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </section>
          )}

          {lesson.check_yourself.length > 0 && (
            <section className="card">
              <h3>Check yourself</h3>
              <ol className="lesson-list">{lesson.check_yourself.map((c, i) => <li key={i}>{c}</li>)}</ol>
            </section>
          )}

          {lesson.findings?.length > 0 && (
            <section className="card">
              <h3>What the group found</h3>
              <ul className="lesson-list">
                {lesson.findings.map((f) => (
                  <li key={f.id}><button className="link" onClick={() => onOpenFinding(f.id)}>{f.title}</button> <span className="muted xs">· {f.verification_status}</span></li>
                ))}
              </ul>
            </section>
          )}

          <p className="muted xs">
            Private to you. Written by the Super Brain{data.creao_run_id ? ` (CREAO run ${data.creao_run_id.slice(0, 8)})` : ""}; papers checked on
            Crossref; names and lines checked against the code sent; pictures drawn by ExpandRG from its descriptions, and plots
            computed from their formulas. The explanation itself isn't verified by anyone: compare it with the code and the paper.
          </p>
        </div>

        <CodePane code={data.code} path={data.path} lit={lit} />
      </div>
    </div>
  );
}

// The steps the code goes through, top to bottom, with the looped ones in a box.
function FlowPicture({ picture, onShow }) {
  const { steps, loop } = picture;
  const box = (s, i) => (
    <li key={`s${i}`} className="flow-step" onMouseEnter={() => s.lines && onShow(s.lines)}>
      <span className="flow-no">{i + 1}</span>
      <span className="flow-text"><strong>{s.label}</strong>{s.detail && <span className="muted xs">{s.detail}</span>}</span>
      <Lines value={s} onShow={onShow} />
    </li>
  );
  const arrow = (i) => <li key={`a${i}`} className="flow-arrow" aria-hidden="true">↓</li>;
  const run = (list, offset) => list.flatMap((s, i) => (i ? [arrow(offset + i), box(s, offset + i)] : [box(s, offset + i)]));
  if (!loop) return <ol className="flow">{run(steps, 0)}</ol>;
  const before = steps.slice(0, loop.from);
  const inside = steps.slice(loop.from, loop.to + 1);
  const after = steps.slice(loop.to + 1);
  return (
    <ol className="flow">
      {run(before, 0)}
      {before.length > 0 && arrow("in")}
      <li className="flow-loop">
        <span className="flow-loop-label">↻ {loop.label || "repeat"}</span>
        <ol className="flow">{run(inside, loop.from)}</ol>
      </li>
      {after.length > 0 && arrow("out")}
      {run(after, loop.to + 1)}
    </ol>
  );
}

// A small grid of points or cells: a stencil, the points an update reads, a boundary.
function GridPicture({ picture, onShow }) {
  const marker = `arrowhead${useId().replace(/[^A-Za-z0-9]/g, "")}`;
  const { rows, cols, cells, arrows, row_label: rowLabel, col_label: colLabel } = picture;
  const size = 54;
  const pad = 28;
  const width = pad + cols * size + 8;
  const height = pad + rows * size + 8;
  const at = (r, c) => [pad + c * size + size / 2, pad + r * size + size / 2];
  const byCell = new Map(cells.map((c) => [`${c.row},${c.col}`, c]));
  return (
    <div className="grid-picture" onMouseEnter={() => picture.lines && onShow(picture.lines)}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={picture.title || "Grid"}>
        <defs>
          <marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="grid-arrowhead" />
          </marker>
        </defs>
        {colLabel && <text x={pad + (cols * size) / 2} y={16} textAnchor="middle" className="grid-axis">{colLabel} →</text>}
        {rowLabel && (
          <text x={14} y={pad + (rows * size) / 2} textAnchor="middle" className="grid-axis"
            transform={`rotate(-90 14 ${pad + (rows * size) / 2})`}>{rowLabel} →</text>
        )}
        {Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => {
          const cell = byCell.get(`${r},${c}`);
          const [x, y] = at(r, c);
          return (
            <g key={`${r},${c}`}>
              <rect x={x - size / 2 + 3} y={y - size / 2 + 3} width={size - 6} height={size - 6} rx="7" className={`grid-cell tone-${cell?.tone || "empty"}`} />
              {cell?.label && <text x={x} y={y + 4} textAnchor="middle" className={`grid-label tone-${cell.tone}`}>{cell.label}</text>}
            </g>
          );
        }))}
        {arrows.map((a, i) => {
          const [x1, y1] = at(...a.from);
          const [x2, y2] = at(...a.to);
          const length = Math.hypot(x2 - x1, y2 - y1);
          const ux = (x2 - x1) / length;
          const uy = (y2 - y1) / length;
          const roomy = length > size * 1.5; // between neighbours a label would cover the cells: it goes below
          return (
            <g key={i}>
              <line x1={x1 + ux * 17} y1={y1 + uy * 17} x2={x2 - ux * 19} y2={y2 - uy * 19} className="grid-arrow" markerEnd={`url(#${marker})`} />
              {a.label && roomy && <text x={(x1 + x2) / 2 + uy * 10} y={(y1 + y2) / 2 - ux * 10 + 4} textAnchor="middle" className="grid-arrow-label">{a.label}</text>}
            </g>
          );
        })}
      </svg>
      {arrows.some((a) => a.label && Math.hypot(a.to[0] - a.from[0], a.to[1] - a.from[1]) <= 1.5) && (
        <p className="muted xs grid-arrow-list">Arrows: {arrows.filter((a) => a.label && Math.hypot(a.to[0] - a.from[0], a.to[1] - a.from[1]) <= 1.5)
          .map((a) => a.label).join(", ")}</p>
      )}
      <Lines value={picture} onShow={onShow} />
    </div>
  );
}

// Curves ExpandRG computed from the lesson's formulas (backend/onboarding/pictures.py); gaps stay gaps.
function PlotPicture({ picture }) {
  const { x, y, curves, marks } = picture;
  const [W, H, L, R, T, B] = [560, 290, 60, 18, 18, 46]; // size and margins
  const values = curves.flatMap((c) => c.points.map((p) => p[1]).filter((v) => v !== null));
  const logY = y.log && values.length > 0 && values.every((v) => v > 0);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) { lo -= Math.abs(lo) || 1; hi += Math.abs(hi) || 1; }
  const fy = (v) => (logY ? Math.log10(v) : v);
  const fx = (v) => (x.log ? Math.log10(v) : v);
  const [y0, y1] = logY ? [fy(lo), fy(hi)] : [lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05];
  const sx = (v) => L + ((fx(v) - fx(x.from)) / (fx(x.to) - fx(x.from))) * (W - L - R);
  const sy = (v) => T + (1 - (fy(v) - y0) / (y1 - y0)) * (H - T - B);
  const xTicks = x.log ? decades(x.from, x.to) : niceTicks(x.from, x.to);
  const yTicks = logY ? decades(lo, hi) : niceTicks(y0, y1);
  const path = (points) => {
    let d = "";
    let pen = false;
    for (const [px, py] of points) {
      if (py === null || (logY && py <= 0)) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${sx(px).toFixed(1)},${sy(py).toFixed(1)} `;
      pen = true;
    }
    return d;
  };
  return (
    <div className="plot-picture">
      <svg viewBox={`0 0 ${W} ${H}`} className="plot-svg" role="img" aria-label={`${picture.title || "Plot"}: ${curves.map((c) => c.label).join(", ")}`}>
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} className="plot-grid" />
            <text x={L - 8} y={sy(t) + 4} textAnchor="end" className="plot-tick">{formatValue(t)}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={`x${t}`} x={sx(t)} y={H - B + 18} textAnchor="middle" className="plot-tick">{formatValue(t)}</text>
        ))}
        <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="plot-axis" />
        <line x1={L} x2={L} y1={T} y2={H - B} className="plot-axis" />
        {marks.map((m, i) => (
          <g key={`m${i}`}>
            <line x1={sx(m.x)} x2={sx(m.x)} y1={T} y2={H - B} className="plot-mark" />
            {m.label && <text x={sx(m.x) + 4} y={T + 10} className="plot-mark-label">{m.label}</text>}
          </g>
        ))}
        {curves.map((c, i) => <path key={i} d={path(c.points)} className="plot-line" style={{ stroke: `var(--slot-${i})` }} />)}
        <text x={L + (W - L - R) / 2} y={H - 6} textAnchor="middle" className="plot-axis-title">{x.label}</text>
        {y.label && <text x={14} y={T + (H - T - B) / 2} textAnchor="middle" className="plot-axis-title"
          transform={`rotate(-90 14 ${T + (H - T - B) / 2})`}>{y.label}</text>}
      </svg>
      <ul className="plot-legend">
        {curves.map((c, i) => (
          <li key={i}><span className="run-swatch" style={{ background: `var(--slot-${i})` }} />{c.label} <code className="xs">{c.expr}</code></li>
        ))}
      </ul>
      <p className="muted xs">Computed by ExpandRG from these formulas at {curves[0]?.points.length || 0} points; gaps are where a formula has no value.</p>
    </div>
  );
}

function niceTicks(lo, hi, count = 5) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => span / s <= count) || 10 * power;
  const ticks = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) ticks.push(Number(t.toPrecision(12)));
  return ticks;
}

function decades(lo, hi) {
  const ticks = [];
  for (let p = Math.ceil(Math.log10(lo)); p <= Math.floor(Math.log10(hi)); p += 1) ticks.push(10 ** p);
  return ticks.length ? ticks : [lo, hi];
}

function Source({ source, references }) {
  const [label, tone] = SOURCE_STATUS[source.status] || SOURCE_STATUS.unchecked;
  const ref = references.find((r) => r.id === source.ref);
  const title = source.title || source.record?.title || ref?.title || ref?.doi || (ref?.arxiv ? `arXiv:${ref.arxiv}` : "Untitled");
  const by = [source.authors || source.record?.authors || ref?.authors, source.year || source.record?.year || ref?.year,
              source.venue || source.record?.venue || ref?.venue].filter(Boolean).join(" · ");
  const differs = source.record?.title && source.title && source.record.title.toLowerCase() !== source.title.toLowerCase();
  return (
    <li className="lesson-source">
      <div>
        {source.url ? <a href={source.url} target="_blank" rel="noreferrer noopener">{title}</a> : <strong>{title}</strong>}
        <span className={`tag source-${tone}`}>{label}</span>
      </div>
      {by && <div className="muted xs">{by}</div>}
      {differs && <div className="muted xs">Crossref's record: “{source.record.title}”{source.record.year ? ` (${source.record.year})` : ""}</div>}
      {source.why && <div className="small">{source.why}</div>}
      {source.note && <div className="muted xs">{source.note}</div>}
    </li>
  );
}

function Lines({ value, onShow }) {
  if (value.lines) {
    const [a, b] = value.lines;
    return <button type="button" className="lines-chip" onClick={() => onShow(value.lines)} onFocus={() => onShow(value.lines)}>
      {a === b ? `line ${a}` : `lines ${a}–${b}`}</button>;
  }
  return value.lines_note ? <span className="muted xs" title={value.lines_note}>lines not in the code sent</span> : null;
}

// LaTeX drawn by KaTeX, which never runs or links anything from it (trust off); until it loads, the source.
function TeX({ katex, latex, display = false }) {
  const html = useMemo(() => {
    if (!katex) return null;
    try {
      return katex.renderToString(latex, { displayMode: display, throwOnError: false, trust: false, strict: "ignore",
        maxSize: 10, maxExpand: 200, output: "htmlAndMathml", errorColor: "#c0392b" });
    } catch {
      return null;
    }
  }, [katex, latex, display]);
  if (!html) return display ? <pre className="tex-source">{latex}</pre> : <code>{latex}</code>;
  return display
    ? <div className="tex-display" dangerouslySetInnerHTML={{ __html: html }} />
    : <span className="tex-inline" dangerouslySetInnerHTML={{ __html: html }} />;
}

// The numbered code that was sent, with the highlighted lines brought into view.
function CodePane({ code, path, lit }) {
  const box = useRef(null);
  const rows = useMemo(() => code.split("\n").map((row) => {
    const match = row.match(/^\s*(\d+)\| ?(.*)$/);
    return match ? { n: Number(match[1]), text: match[2] } : { gap: true };
  }), [code]);
  useEffect(() => {
    if (!lit || !box.current) return;
    const first = box.current.querySelector(`[data-line="${lit[0]}"]`);
    if (first) box.current.scrollTop = first.offsetTop - box.current.clientHeight / 3;
  }, [lit]);
  return (
    <aside className="lesson-code" aria-label={`The code: ${path}`}>
      <div className="lesson-code-head mono small">{path}</div>
      <div className="code-lines" ref={box}>
        {rows.map((r, i) => r.gap
          ? <div key={i} className="code-gap">⋯</div>
          : <div key={i} data-line={r.n} className={`code-line ${lit && r.n >= lit[0] && r.n <= lit[1] ? "lit" : ""}`}>
              <span className="code-no">{r.n}</span><span className="code-text">{r.text || " "}</span>
            </div>)}
      </div>
    </aside>
  );
}
