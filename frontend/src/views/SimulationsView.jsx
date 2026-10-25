import { useEffect, useState } from "react";
import { backend, connector } from "../api.js";
import { ErrorNote, FolderPicker, Markdown } from "../components.jsx";
import RunsView from "./RunsView.jsx";

const labels = { awaiting_setup: "Preparing setup", needs_input: "Needs attention", ready: "Ready to run",
  queued: "Queued", running: "Running", finished: "Finished", failed: "Failed", stopped: "Stopped", interrupted: "Interrupted" };

// The tab ("setup" or "monitor") is set by the sidebar too, so it lives in the app.
export default function SimulationsView({ session, project, tab, onTab: setTab, onVisualize, onDiagnose }) {
  const [root, setRoot] = useState("");
  const [environment, setEnvironment] = useState("");
  const [compiler, setCompiler] = useState("");
  const [selectedEnvironment, setSelectedEnvironment] = useState("");
  const [selectedCompiler, setSelectedCompiler] = useState("");
  const [request, setRequest] = useState("");
  const [versionNote, setVersionNote] = useState(""); // more about the version on screen, for its update
  const [versions, setVersions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [monitorId, setMonitorId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const token = session.token;

  useEffect(() => {
    setSelectedEnvironment("");
    setSelectedCompiler("");
    setVersionNote("");
  }, [selectedId]);

  useEffect(() => {
    let closed = false;
    async function refresh() {
      try {
        const list = await connector(`/simulations?project_id=${encodeURIComponent(project.id)}`, { token });
        if (closed) return;
        setVersions(list);
        const id = selectedId || list[0]?.id;
        if (!id) { setDetail(null); return; }
        if (!selectedId) setSelectedId(id);
        const item = await connector(`/simulations/${id}`, { token });
        if (!closed) setDetail(item);
      } catch (e) {
        if (!closed) setError(e.message);
      }
    }
    refresh();
    const timer = setInterval(refresh, 1500);
    return () => { closed = true; clearInterval(timer); };
  }, [project.id, token, selectedId]);

  // Reconnect to a saved setup job after navigating away or reloading. Never resubmit it.
  useEffect(() => {
    if (!detail?.setup_job_id || detail.status !== "awaiting_setup") return;
    let closed = false, timer;
    const id = detail.id, jobId = detail.setup_job_id;
    async function poll() {
      try {
        const job = await backend(`/jobs/${jobId}`, { token });
        if (closed) return;
        if (job.status === "running") { timer = setTimeout(poll, 750); return; }
        if (job.status === "failed") throw new Error(job.error?.message || "Your agent could not prepare this setup.");
        const saved = await connector(`/simulations/${id}/plan`, {
          method: "POST", token, body: { plan: job.diagnosis.plan },
        });
        if (!closed) { setDetail(saved); setError(""); }
      } catch (e) {
        if (!closed) {
          setError(e.message);
          try {
            const saved = await connector(`/simulations/${id}/planning`, { method: "POST", token, body: { error: e.message } });
            if (!closed) setDetail(saved);
          } catch { /* show the original error; a saved job is not resubmitted */ }
        }
      }
    }
    poll();
    return () => { closed = true; clearTimeout(timer); };
  }, [detail?.id, detail?.setup_job_id, detail?.status, token]);

  async function askAgent(record, context, note = request) {
    const started = await backend("/simulation-setup", { method: "POST", token,
      body: { project_id: project.id, ...context, request: note } });
    const saved = await connector(`/simulations/${record.id}/planning`, {
      method: "POST", token, body: { job_id: started.job_id },
    });
    setDetail(saved);
  }

  async function prepare() {
    setBusy(true); setError("");
    try {
      const result = await connector("/simulations/inspect", { method: "POST", token,
        body: { root, project_id: project.id, ...environmentSelection(environment, compiler) } });
      setSelectedId(result.simulation.id); setDetail(result.simulation);
      await askAgent(result.simulation, result.context);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  // Update the version on screen with what you tell your agent about it. A version that already ran
  // keeps its setup as it was (it's the record of that run): the update goes to a copy, the next version.
  async function updateSetup() {
    setBusy(true); setError("");
    try {
      let target = detail;
      if (!editable) {
        target = await connector(`/simulations/${detail.id}/repeat`, { method: "POST", token });
        setSelectedId(target.id); setDetail(target);
      }
      const context = await connector(`/simulations/${target.id}/context`, { token });
      await askAgent(target, context, versionNote.trim());
      setVersionNote("");
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function deleteVersion() {
    if (!window.confirm(`Delete Version ${detail.version}? Its saved code, inputs and output are removed from this computer. `
      + "Your project folder isn't touched, and results already in Monitor stay.")) return;
    setBusy(true); setError("");
    try {
      await connector(`/simulations/${detail.id}`, { method: "DELETE", token });
      setDetail(null); setSelectedId(null);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function act(action) {
    setBusy(true); setError("");
    try {
      const result = await connector(`/simulations/${detail.id}/${action}`, { method: "POST", token });
      setDetail(result); setSelectedId(result.id);
      if (action === "run") { setMonitorId(result.backend_run_id); setTab("monitor"); }
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function recheck() {
    setBusy(true); setError("");
    try {
      setDetail(await connector(`/simulations/${detail.id}/environment`, { method: "POST", token, body: {} }));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function useEnvironment() {
    setBusy(true); setError("");
    try {
      const saved = await connector(`/simulations/${detail.id}/environment`, { method: "POST", token,
        body: environmentSelection(selectedEnvironment, selectedCompiler) });
      setDetail(saved);
      const context = await connector(`/simulations/${saved.id}/context`, { token });
      await askAgent(saved, context);
      setSelectedEnvironment(""); setSelectedCompiler("");
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  const planning = detail?.status === "awaiting_setup" && detail?.setup_job_id;
  const active = ["queued", "running"].includes(detail?.status);
  const editable = detail && ["awaiting_setup", "needs_input", "ready"].includes(detail.status);
  const plan = detail?.plan;
  const shownVersions = detail
    ? [...versions.filter((v) => v.id !== detail.id), detail].sort((a, b) => b.version - a.version)
    : versions;

  return <div className="view simulation-page">
    <div className="simulation-heading"><div><h2>Simulations</h2>
      <p className="lead">Choose an existing simulation project. ExpandRG uses its environment and prepares the run. You decide when to start.</p></div>
      <div className="sb-tabs" role="tablist" aria-label="Simulation workflow">
        <button role="tab" aria-selected={tab === "setup"} className={tab === "setup" ? "active" : ""} onClick={() => setTab("setup")}>Set up &amp; run</button>
        <button role="tab" aria-selected={tab === "monitor"} className={tab === "monitor" ? "active" : ""} onClick={() => setTab("monitor")}>Monitor</button>
      </div>
    </div>
    {tab === "monitor" ? <RunsView session={session} project={project} initialRunId={monitorId} onVisualize={onVisualize} onDiagnose={onDiagnose} /> : <>
      <section className="card simulation-prepare">
        <div className="simulation-step">01 · PREPARE A VERSION</div>
        <h3>What are you running?</h3>
        <FolderPicker root={root} onChange={setRoot} />
        <details className="simulation-environment"><summary>Existing environment <span className="muted small">· Optional</span></summary>
          <p className="muted small">ExpandRG detects the project’s Python environment and available build tools automatically. Connect an existing environment here if your project uses another location.</p>
          <EnvironmentFields prefix="prepare" environment={environment} onEnvironment={setEnvironment}
            compiler={compiler} onCompiler={setCompiler} disabled={busy || planning} />
        </details>
        <label className="simulation-request">Anything your agent should know? <span className="muted">Optional</span>
          <textarea rows="2" maxLength={2000} value={request} onChange={(e) => setRequest(e.target.value)}
            placeholder="For example: use the small example input, or run the Fortran heat solver." />
        </label>
        <div className="simulation-prepare-actions"><button className="primary" disabled={!root || busy || planning} onClick={prepare}>
          {busy ? "Preparing…" : "Detect & prepare"}</button>
          <span className="muted small">ExpandRG AI checks how to run your existing project and saves its code and inputs as a version. Powered by CREAO; nothing runs yet.</span></div>
      </section>
      <ErrorNote error={error} />
      <div className="simulation-workspace">
        <nav className="run-list simulation-versions" aria-label="Saved simulation versions"><h4>Saved versions</h4>
          {!shownVersions.length && <p className="muted small">Your first version will appear here. Earlier versions and results are kept.</p>}
          {shownVersions.map((v) => <button key={v.id} className={`run-item ${v.id === selectedId ? "selected" : ""}`} onClick={() => { setSelectedId(v.id); setError(""); }}>
            <strong>Version {v.version}</strong><span>{v.plan?.title || v.folder}</span>
            <span className="muted small">{labels[v.status]} · {new Date(v.created_at).toLocaleString()}</span>
          </button>)}
        </nav>
        <section className="card simulation-version">
          {!detail ? <div className="simulation-empty"><span className="simulation-step">02 · REVIEW &amp; RUN</span>
            <h3>Run the project you already have</h3><p className="muted">ExpandRG checks the project’s existing environment, build instructions, and inputs. Fortran projects use the compiler already available in your environment. Output connects to Monitor automatically.</p></div> : <>
            <div className="card-header"><div><span className="simulation-step">VERSION {detail.version}</span>
              <h3>{plan?.title || detail.folder}</h3></div><div className="simulation-version-controls">
                <span className={`tag ${active ? "live" : ""}`}>{labels[detail.status]}</span>
                {editable && <button className="primary" disabled={busy || detail.status !== "ready"} onClick={() => act("run")}>Run simulation</button>}
                {active && <button disabled={busy} onClick={() => act("stop")}>Stop this run</button>}
              </div></div>
            <p className="muted small">{detail.file_count} files saved · code and inputs kept for this version
              {detail.source_version && ` · prepared from Version ${detail.source_version}`}</p>
            {detail.runtime && <>
              <p className="muted small">Environment: {detail.runtime.label}</p>
              <details className="simulation-environment"><summary>Environment details &amp; connection</summary>
                <p className="muted small">These local paths show which existing tools this version uses.</p>
                <dl className="simulation-runtime-paths">
                  <dt>Python interpreter</dt><dd><code>{detail.runtime.python}</code></dd>
                  {Object.entries(detail.runtime.tools || {}).filter(([name]) => ["{fortran}", "{cc}", "{cxx}"].includes(name)).map(([name, path]) =>
                    <div key={name}><dt>{({ "{fortran}": "Fortran compiler", "{cc}": "C compiler", "{cxx}": "C++ compiler" })[name]}</dt><dd><code>{path}</code></dd></div>)}
                </dl>
                {editable && <>
                  <p className="muted small">To use a different installed environment or compiler, enter its path below. Blank fields keep this version’s current selection.</p>
                  <EnvironmentFields prefix="version" environment={selectedEnvironment} onEnvironment={setSelectedEnvironment}
                    compiler={selectedCompiler} onCompiler={setSelectedCompiler} disabled={busy || planning} />
                  <button disabled={busy || planning} onClick={useEnvironment}>Use environment &amp; update setup</button>
                  <p className="muted small">Your agent reviews the setup again through CREAO. Recheck existing tools below refreshes detection without asking the agent.</p>
                </>}
              </details>
            </>}
            {planning && <p role="status" className="simulation-progress">Your agent is reading the saved project and preparing its build/run setup…</p>}
            {detail.status === "awaiting_setup" && !planning && <p className="muted">This version is saved. Use Update setup to ask your agent how to run it.</p>}
            {plan && <><p className="simulation-language">{plan.language}</p><Markdown text={plan.summary} />
              {plan.environment_notes.length > 0 && <ul>{plan.environment_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
              {plan.questions.length > 0 && <div className="notice"><strong>Your agent needs a detail</strong><ul>{plan.questions.map((q, i) => <li key={i}>{q}</li>)}</ul><p>Answer in the box below, then choose Update setup.</p></div>}
              {detail.missing_tools.length > 0 && <div className="notice"><strong>Tools not found in selected environment</strong><p>{detail.missing_tools.join(", ")}</p><p>Connect the environment or compiler your project already uses in Environment details &amp; connection. If you activate it in a terminal, restart ExpandRG from that terminal, then recheck existing tools.</p></div>}
              <details className="simulation-commands" open><summary>Build &amp; run steps</summary>
                {plan.build_commands.map((c, i) => <div key={i}><span className="label">Build {i + 1}</span><pre>{displayCommand(c)}</pre></div>)}
                {plan.run_command.length > 0 && <div><span className="label">Run</span><pre>{displayCommand(plan.run_command)}</pre></div>}
                <p className="muted small">Working folder: {plan.working_directory}. Output is captured automatically. Run uses this saved version, so later edits to your project do not change it.</p>
              </details>
              <p className="muted small">Based on {plan.source_files.join(", ")}. Run limit: {detail.limits.wall_seconds / 60} minutes, {detail.limits.memory_mb / 1024} GB memory.</p>
            </>}
            {detail.reason && <p className="notice">{detail.reason}</p>}
            {!active && (
              <div className="simulation-update">
                <label htmlFor="version-note">Tell your agent more about this version <span className="muted">Optional</span></label>
                <textarea id="version-note" rows="3" maxLength={2000} value={versionNote} onChange={(e) => setVersionNote(e.target.value)}
                  disabled={busy || planning}
                  placeholder="For example: build with mingw32-make, run swimmer.exe with 4 threads, or answer your agent's question." />
                <div className="simulation-update-actions">
                  <button className="primary" disabled={busy || planning} onClick={updateSetup}>
                    {editable ? "Update setup" : "Update as a new version"}</button>
                  <span className="muted small">{editable
                    ? "Your agent reads this version again with your note and updates its build and run steps. Nothing runs yet."
                    : `This version already ran, so its setup stays as it was. Your note updates a copy, Version ${Math.max(...shownVersions.map((v) => v.version)) + 1}.`}</span>
                </div>
              </div>
            )}
            <div className="actions simulation-run-actions">
              {editable && plan && <button disabled={busy || planning} onClick={recheck}>Recheck existing tools</button>}
              {!active && plan && !planning && <button disabled={busy} onClick={() => act("repeat")}>Prepare from this version</button>}
              {detail.backend_run_id && <button onClick={() => { setMonitorId(detail.backend_run_id); setTab("monitor"); }}>Open in Monitor</button>}
              {!active && <button className="link danger-link" disabled={busy || planning} onClick={deleteVersion}>Delete this version</button>}
            </div>
            {detail.stage && <p className="muted small">{active ? detail.stage : `Last phase: ${detail.stage}`}{detail.seconds != null && ` · ${detail.seconds}s`}</p>}
            {detail.output && <details className="simulation-output" open={active}><summary>Output</summary><pre>{detail.output}</pre></details>}
          </>}
        </section>
      </div>
    </>}
  </div>;
}

function environmentSelection(environment, compiler) {
  return { ...(environment.trim() && { environment: environment.trim() }), ...(compiler.trim() && { compiler: compiler.trim() }) };
}

function EnvironmentFields({ prefix, environment, onEnvironment, compiler, onCompiler, disabled }) {
  return <div className="simulation-environment-fields">
    <label htmlFor={`${prefix}-environment`}>Environment directory or Python executable
      <input id={`${prefix}-environment`} value={environment} onChange={(e) => onEnvironment(e.target.value)}
        disabled={disabled} placeholder="Path to an existing environment or Python executable" autoComplete="off" spellCheck="false" />
    </label>
    <label htmlFor={`${prefix}-compiler`}>Fortran compiler executable <span className="muted small">Optional</span>
      <input id={`${prefix}-compiler`} value={compiler} onChange={(e) => onCompiler(e.target.value)}
        disabled={disabled} placeholder="Path to the existing compiler, if needed" autoComplete="off" spellCheck="false" />
    </label>
  </div>;
}

function displayCommand(words) {
  return words.map((w) => w === "{python}" ? "python" : /\s/.test(w) ? JSON.stringify(w) : w).join(" ");
}
