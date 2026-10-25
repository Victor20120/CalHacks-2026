import { useEffect, useRef, useState } from "react";
import { BrainCircuit, Download, FilePlus2, Save, Undo2, X } from "lucide-react";
import { backend, remember } from "../api.js";
import { ErrorNote, Markdown } from "../components.jsx";
import { formatValue } from "../charts.jsx";

// The Visualization Studio: graphs of your runs and experiments. ExpandRG's toolkit draws them
// (backend/visualization/); every change, yours or your agent's, is an operation it checks first.
// Plotly loads only on this page.

const PLOT_CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "toImage"] };

export default function VisualizeView({ session, project, seed }) {
  const [catalog, setCatalog] = useState(null);
  const [catalogNotes, setCatalogNotes] = useState([]);
  const [spec, setSpec] = useState(null);
  const [view, setView] = useState(null); // { figure, notes, stats, comparisons, metrics }
  const [refused, setRefused] = useState([]);
  const [saved, setSaved] = useState([]);
  const [current, setCurrent] = useState(null); // the open saved graph: { id, title, conversation }
  const [undo, setUndo] = useState([]); // earlier specs of an unsaved graph
  const [busy, setBusy] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);
  const [error, setError] = useState("");
  const plot = useRef(null); // { plotly, node }, for export
  const token = session.token;
  const isLeader = session.member.role === "leader";
  const locked = busy || agentBusy;
  const lastGraph = `expandrg.graph.${session.member.id}.${project.id}`; // reopened when you come back to this page

  async function loadSaved() {
    setSaved(await backend(`/visualizations?project_id=${encodeURIComponent(project.id)}`, { token }));
  }

  useEffect(() => {
    let stopped = false;
    setCatalog(null);
    setSpec(null);
    setView(null);
    setCurrent(null);
    setUndo([]);
    setRefused([]);
    (async () => {
      try {
        const start = await backend(`/visualize/datasets?project_id=${encodeURIComponent(project.id)}`, { token });
        if (stopped) return;
        setCatalog(start.datasets);
        setCatalogNotes(start.notes);
        loadSaved().catch(() => {});
        if (seed?.graph_id) {
          const graph = await backend(`/visualizations/${encodeURIComponent(seed.graph_id)}`, { token });
          if (!stopped) show(graph);
          return;
        }
        const wanted = seed?.datasets?.filter((d) => start.datasets.some((x) => x.id === d)) || [];
        if (wanted.length) {
          await change([{ op: "create_graph", datasets: wanted, ...(seed.metrics?.length ? { metrics: seed.metrics } : {}) }], start.spec);
          return;
        }
        if (remember.get(lastGraph)) {
          try {
            const graph = await backend(`/visualizations/${remember.get(lastGraph)}`, { token });
            if (!stopped) show(graph);
            return;
          } catch {
            remember.set(lastGraph, null); // deleted since
          }
        }
        setSpec(start.spec);
        setView(await backend("/visualize/figure", { method: "POST", token, body: { spec: start.spec } }));
      } catch (e) {
        if (!stopped) setError(e.message);
      }
    })();
    return () => { stopped = true; };
  }, [project.id, seed?.at]);

  // Every change: checked operations, then the redrawn figure. A saved graph keeps its history.
  async function change(ops, from = spec) {
    setBusy(true);
    setError("");
    try {
      const result = await backend("/visualize/ops", { method: "POST", token, body: { spec: from, ops } });
      setRefused(result.refused);
      if (result.applied.length) {
        if (current) {
          const kept = await backend(`/visualizations/${current.id}`, { method: "PUT", token, body: { spec: result.spec, ops: result.applied } });
          setCurrent((c) => ({ ...c, title: kept.title }));
        } else if (from) {
          setUndo((u) => [...u.slice(-29), from]);
        }
      }
      setSpec(result.spec);
      setView(result);
      for (const action of result.actions) if (action.export) exportAs(action.export, result.figure);
      return result;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function undoLast() {
    setError("");
    try {
      if (current) {
        show(await backend(`/visualizations/${current.id}/undo`, { method: "POST", token }));
      } else if (undo.length) {
        const previous = undo[undo.length - 1];
        setUndo(undo.slice(0, -1));
        const drawn = await backend("/visualize/figure", { method: "POST", token, body: { spec: previous } });
        setSpec(drawn.spec);
        setView(drawn);
      }
    } catch (e) {
      setError(e.message);
    }
  }

  function show(graph) {
    setSpec(graph.spec);
    setView(graph);
    setRefused([]);
    setCurrent({ id: graph.id, title: graph.title, conversation: graph.conversation || [] });
    remember.set(lastGraph, graph.id);
  }

  async function saveAs(title) {
    const graph = await backend("/visualizations", { method: "POST", token, body: { title, spec } });
    show(graph);
    setUndo([]);
    loadSaved().catch(() => {});
    return graph;
  }

  async function open(id) {
    setError("");
    try {
      show(await backend(`/visualizations/${id}`, { token }));
      setUndo([]);
    } catch (e) {
      setError(e.message);
    }
  }

  async function startNew() {
    setCurrent(null);
    remember.set(lastGraph, null);
    setUndo([]);
    setRefused([]);
    const fresh = { ...spec, datasets: [], colors: {}, metrics: [], range: null, annotations: [], x: "step", type: "line", layout: "overlay",
      baseline: null, difference: "absolute", relative_threshold: 1e-12, histogram: { bins: 30 },
      sweep: { parameter: "", step: null }, resolution: "recorded" };
    const drawn = await backend("/visualize/figure", { method: "POST", token, body: { spec: fresh } });
    setSpec(drawn.spec);
    setView(drawn);
  }

  async function remove() {
    if (!current || !window.confirm(`Delete the saved graph "${current.title}"? The runs themselves aren't touched.`)) return;
    await backend(`/visualizations/${current.id}`, { method: "DELETE", token });
    remember.set(lastGraph, null);
    setCurrent(null);
    loadSaved().catch(() => {});
  }

  // Exports are drawn light on white whatever the page's theme, so they read on paper and slides.
  function exportAs(format, figure = view?.figure) {
    if (!plot.current || !figure) return;
    const { plotly, node } = plot.current;
    const paper = themed(figure, (name) => PAPER[name] || "");
    paper.layout.paper_bgcolor = paper.layout.plot_bgcolor = "#ffffff";
    const width = spec?.style?.preset === "poster" ? 1800 : 1200;
    plotly.downloadImage(paper, {
      format, filename: (spec?.style?.title || current?.title || "expandrg-graph").replace(/[^\w.-]+/g, "_"),
      width, height: Math.round((node.offsetHeight / Math.max(1, node.offsetWidth)) * width),
    });
  }

  function toggleRun(item) {
    if (spec.datasets.includes(item.id)) return change([{ op: "remove_run", dataset: item.id }]);
    if (!spec.datasets.length) return change([{ op: "create_graph", datasets: [item.id], ...(item.metrics[0] ? { metrics: [item.metrics[0]] } : {}) }]);
    return change([{ op: "add_run", dataset: item.id }]);
  }

  if (!catalog || !spec) {
    return <div className="view"><h2>Visualize</h2><p className="muted">Loading your runs…</p><ErrorNote error={error} /></div>;
  }
  const shown = catalog.filter((d) => spec.datasets.includes(d.id));
  const measures = [...new Set(shown.flatMap((d) => d.metrics))];
  const active = view?.metrics || spec.metrics;
  const sweepParameters = [...new Set(shown.filter((d) => d.source === "experiment")
    .flatMap((d) => Object.entries(d.parameters || {}).filter(([, value]) => typeof value === "number" && Number.isFinite(value)).map(([key]) => key)))];
  const ordered = ["line", "scatter"].includes(spec.type);
  const fullLogs = spec.resolution === "full";

  function changeType(type) {
    if (type === "histogram") return change([{ op: "configure_histogram", bins: spec.histogram?.bins || 30 }]);
    if (type === "sweep" && (spec.sweep?.parameter || sweepParameters[0])) {
      return change([{ op: "configure_sweep", parameter: spec.sweep?.parameter || sweepParameters[0], step: spec.sweep?.step ?? null }]);
    }
    return change([{ op: "change_graph_type", type }]);
  }

  return (
    <div className="view visualize">
      <div className="visualize-head">
        <div>
          <h2>Visualize</h2>
          <p className="muted">
            Compare runs and experiments on one graph. ExpandRG draws every graph from the runs' own numbers: nothing is
            invented, and the runs are never changed.{isLeader ? "" : " Ask your agent to change a graph in the panel on the right."}
          </p>
        </div>
        <div className="visualize-files">
          <select value={current?.id || ""} disabled={locked} onChange={(e) => (e.target.value ? open(e.target.value) : startNew())} aria-label="Saved graphs">
            <option value="">{current ? "New graph" : "Saved graphs…"}</option>
            {saved.map((g) => <option key={g.id} value={g.id}>{g.title}</option>)}
          </select>
          <button onClick={startNew} disabled={locked} title="New graph"><FilePlus2 /> New</button>
          <SaveButton current={current} spec={spec} onSave={saveAs} disabled={locked} />
          <button onClick={undoLast} disabled={locked || (!current && !undo.length)} title="Undo the last change"><Undo2 /> Undo</button>
          {current && <button className="link" disabled={locked} onClick={() => remove().catch((e) => setError(e.message))}>Delete</button>}
        </div>
      </div>
      <ErrorNote error={error} />

      <div className={`visualize-grid ${isLeader ? "no-agent" : ""}`}>
        <div className="graph-column">
        <div className="visualize-top">
        <section className="card run-picker">
          <h3>Runs</h3>
          {!catalog.length && <p className="muted small">No runs or experiments in {project.name} yet. Watch a run or try an experiment first.</p>}
          {catalogNotes.map((n) => <p key={n} className="muted small">{n}</p>)}
          <ul className="run-choices">
            {catalog.map((item) => {
              const on = spec.datasets.includes(item.id);
              const slot = spec.colors[item.id];
              return (
                <li key={item.id}>
                  <label className={`check run-choice ${on ? "on" : ""}`}>
                    <input type="checkbox" checked={on} disabled={locked} onChange={() => toggleRun(item)} />
                    <span className="run-swatch" style={on && slot !== undefined ? { background: `var(--slot-${slot})` } : undefined} />
                    <span className="run-choice-text">
                      <span className="run-choice-label">{item.label}</span>
                      <span className="muted xs">
                        {item.source === "run" ? "Watched run" : item.source === "experiment" ? "Experiment" : "Original run"}
                        {item.researcher && item.researcher !== session.member.display_name ? ` · ${item.researcher}` : ""}
                        {item.started ? ` · ${item.started.slice(0, 10)}` : ""}
                        {item.alerts?.critical ? ` · ${item.alerts.critical} critical` : ""}
                      </span>
                      {Object.keys(item.parameters || {}).length > 0 && (
                        <span className="xs mono">{Object.entries(item.parameters).map(([k, v]) => `${k} = ${v}`).join(", ")}</span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </section>

          <section className="card graph-controls">
            <div className="control-row">
              <div className="field-label">Measures</div>
              <div className="chips">
                {measures.map((m) => (
                  <button key={m} className={`chip ${active.includes(m) ? "on" : ""}`} disabled={locked}
                    onClick={() => {
                      const next = active.includes(m) ? active.filter((x) => x !== m) : [...active, m];
                      if (next.length) change([{ op: "change_metric", metrics: next }]);
                    }}>{m}</button>
                ))}
                {!measures.length && <span className="muted small">Pick a run on the left.</span>}
              </div>
            </div>
            <div className="control-row wrap">
              <label className="inline">X axis
                <select value={spec.x} disabled={locked || !ordered} onChange={(e) => change([{ op: "change_axis", x: e.target.value }])}>
                  <option value="step">Step</option>
                  {measures.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="inline">Graph
                <select aria-label="Graph type" value={spec.type} disabled={locked} onChange={(e) => changeType(e.target.value)}>
                  <option value="line">Line</option><option value="scatter">Scatter</option>
                  <option value="histogram">Histogram</option><option value="sweep">Parameter sweep</option>
                </select>
              </label>
              <Segmented label="Runs" value={spec.layout} disabled={locked || !ordered}
                options={[["overlay", "Together"], ["separate", "Separate"], ["difference", "Difference"]]}
                onChange={(mode) => change([{ op: "compare_runs", mode }])} />
              {!ordered && <Segmented label={spec.type === "histogram" ? "Value scale" : "Parameter scale"} value={spec.x_scale} disabled={locked}
                options={[["linear", "Linear"], ["log", "Log"]]}
                onChange={(x_scale) => change([{ op: "change_axis", x_scale }])} />}
              <Segmented label={spec.type === "histogram" ? "Count scale" : "Y scale"} value={spec.y_scale} disabled={locked} options={[["linear", "Linear"], ["log", "Log"]]}
                onChange={(y_scale) => change([{ op: "change_axis", y_scale }])} />
              <label className="inline">Style
                <select value={spec.style.preset} disabled={locked} onChange={(e) => change([{ op: "update_style", preset: e.target.value }])}>
                  <option value="screen">Screen</option>
                  <option value="paper">Paper</option>
                  <option value="poster">Poster</option>
                </select>
              </label>
            </div>
            {ordered && <div className="viz-special-controls">
              <label className="inline">Baseline
                <select aria-label="Baseline run" value={spec.baseline || ""} disabled={locked || !shown.length}
                  onChange={(e) => change([{ op: "set_baseline", dataset: e.target.value }])}>
                  <option value="" disabled>Choose a baseline</option>
                  {shown.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
                </select>
              </label>
              {spec.layout === "difference" && <>
                <Segmented label="Difference" value={spec.difference || "absolute"} disabled={locked}
                  options={[["absolute", "Absolute"], ["relative", "Relative"]]}
                  onChange={(difference) => change([{ op: "compare_runs", mode: "difference", difference }])} />
                {spec.difference === "relative" && <NumberSetting label="Minimum |baseline|" value={spec.relative_threshold ?? 1e-12}
                  min={0} step="any" disabled={locked}
                  onCommit={(threshold) => change([{ op: "compare_runs", mode: "difference", difference: "relative", threshold }])} />}
                <p className="muted xs">Differences use overlapping data only. Values estimated between recorded points are identified in the notes below.</p>
              </>}
            </div>}
            {spec.type === "histogram" && <div className="viz-special-controls">
              <NumberSetting label="Histogram bins" value={spec.histogram?.bins || 30} min={1} max={200} step={1} disabled={locked}
                onCommit={(bins) => change([{ op: "configure_histogram", bins }])} />
              <p className="muted xs">Each measure uses the same bin edges across runs. Every finite value contributes; the graph's notes explain excluded values.</p>
            </div>}
            {spec.type === "sweep" && <SweepControl parameter={spec.sweep?.parameter || ""} at={spec.sweep?.step ?? null}
              parameters={sweepParameters} disabled={locked}
              onApply={(parameter, step) => change([{ op: "configure_sweep", parameter, step }])} />}
            <div className="control-row wrap">
              <RangeControl range={spec.range} x={spec.type === "sweep" ? spec.sweep?.parameter || "parameter" : spec.x}
                source={spec.type === "histogram"} disabled={locked}
                onApply={(from, to) => change([{ op: "set_time_range", from, to }])} onClear={() => change([{ op: "set_time_range", clear: true }])} />
              <TextSetting label="Title" value={spec.style.title} max={120} disabled={locked}
                onCommit={(title) => change([{ op: "update_style", title }])} />
            </div>
            {!isLeader && shown.some((d) => d.source === "run") && <div className="viz-resolution">
              <div className="control-row wrap">
                <span className="field-label">Data: {fullLogs ? "full local logs requested" : "recorded points"}</span>
                <button disabled={locked} onClick={() => change([{ op: "set_resolution", mode: fullLogs ? "recorded" : "full" }])}>
                  {fullLogs ? "Use recorded points" : "Read full local run logs"}
                </button>
              </div>
              <p className="muted xs">Reads the selected runs' granted local logs without restarting a simulation. If a log is unavailable, the graph says which recorded data it used.</p>
            </div>}
            <details className="more-controls">
              <summary>Units and notes</summary>
              <div className="control-row wrap">
                {["step", ...active].map((m) => (
                  <TextSetting key={m} label={`Unit of ${m}`} value={spec.units[m] || ""} max={24} disabled={locked} small
                    onCommit={(unit) => change([{ op: "set_units", measure: m, unit }])} />
                ))}
              </div>
              <p className="muted xs">Only you set units, so they're never guessed. Without one, the axis shows the measure's name.</p>
              <NoteControl annotations={spec.annotations} disabled={locked}
                onAdd={(x, text) => change([{ op: "add_annotation", x, text }])}
                onRemove={(index) => change([{ op: "remove_annotation", index }])} />
            </details>
          </section>
        </div>

          <section className="card graph-card">
            <div className="card-header">
              <h3>{current ? current.title : "Graph"}{busy ? <span className="muted small"> · updating…</span> : null}</h3>
              <div className="row-buttons">
                <button onClick={() => exportAs("png")} disabled={!spec.datasets.length}><Download /> PNG</button>
                <button onClick={() => exportAs("svg")} disabled={!spec.datasets.length}><Download /> SVG</button>
              </div>
            </div>
            {spec.datasets.length ? <Plot figure={view?.figure} spec={spec} onReady={(p) => { plot.current = p; }} />
              : <p className="muted empty-graph">Pick one or more runs on the left to draw them.</p>}
            {refused.length > 0 && (
              <div className="warning-note">
                {refused.map((r, i) => <div key={i}>Not applied: {r.reason}</div>)}
              </div>
            )}
            {view?.notes?.length > 0 && <ul className="graph-notes">{view.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
            {view?.comparisons?.length > 0 && (
              <ul className="graph-comparisons">
                {view.comparisons.map((c, i) => (
                  <li key={i}>
                    <strong>{c.metric}</strong>: {c.other} differs from {c.base} by at most{" "}
                    <span className="mono">{formatValue(c.max_abs_diff)}</span>
                    {c.at !== null && <> at {spec.x === "step" ? "step" : spec.x} <span className="mono">{formatValue(c.at)}</span></>},
                    compared over {formatValue(c.from)}–{formatValue(c.to)} where both have values
                    {c.interpolated ? " (straight-line values between measured points where their steps differ)" : ""}.
                  </li>
                ))}
              </ul>
            )}
            {view?.stats?.length > 0 && <StatsTable rows={view.stats} x={spec.type === "sweep" ? spec.sweep?.parameter || "parameter" : spec.x} />}
            <PublicationExport spec={spec} token={token} disabled={locked || !spec.datasets.length} />
          </section>
        </div>

        {!isLeader && (
          <GraphAgent session={session} spec={spec} current={current} focus={seed?.agent} onBusyChange={setAgentBusy}
            ensureSaved={async () => current || saveAs(spec.style.title || `Graph ${new Date().toISOString().slice(0, 16).replace("T", " ")}`)}
            onResult={(result) => {
              setSpec(result.spec);
              setView(result);
              setRefused(result.refused || []);
              setCurrent((c) => c && { ...c, conversation: [...c.conversation, result] });
              for (const action of result.actions || []) if (action.export) exportAs(action.export, result.figure);
            }} />
        )}
      </div>
    </div>
  );
}

// The light theme's tokens, for exports.
const PAPER = { "--text": "#17181a", "--text-2": "#4b4f56", "--muted": "#6f737a", "--grid": "#ecebe7",
  "--border": "#e6e5e1", "--border-strong": "#d8d7d2", "--surface": "#ffffff" };

// The figure in a theme: run colours by palette slot, text and grid from the theme's tokens. Without a
// slot token, a run keeps the figure's own colour (the light palette in backend/visualization/figure.py).
function themed(figure, token) {
  const data = figure.data.map((t) => {
    const color = (t.meta?.aggregate ? token("--muted") : token(`--slot-${t.meta?.slot ?? 0}`)) || undefined;
    return {
      ...t,
      ...(t.line ? { line: { ...t.line, ...(color ? { color } : {}) } } : {}),
      ...(t.marker ? { marker: { ...t.marker, ...(color ? { color } : {}), line: { ...t.marker.line, color: token("--surface") } } } : {}),
    };
  });
  const axis = (a) => ({ ...a, gridcolor: token("--grid"), linecolor: token("--border-strong"), tickcolor: token("--border-strong"),
    color: token("--muted"), ...(a.title ? { title: { ...a.title, font: { color: token("--text-2") } } } : {}) });
  const layout = Object.fromEntries(Object.entries(figure.layout).map(([k, v]) => [k, /^[xy]axis\d*$/.test(k) ? axis(v) : v]));
  layout.paper_bgcolor = "rgba(0,0,0,0)";
  layout.plot_bgcolor = "rgba(0,0,0,0)";
  layout.font = { ...layout.font, family: "Inter, system-ui, sans-serif", color: token("--text") };
  layout.hoverlabel = { bgcolor: token("--surface"), bordercolor: token("--border"), font: { color: token("--text") } };
  layout.legend = { ...layout.legend, font: { color: token("--text-2") } };
  layout.annotations = (layout.annotations || []).map((a) => ({
    ...a, font: { ...a.font, color: a.meta === "marker-label" || a.meta === "panel-title" ? token("--muted") : token("--text-2") },
  }));
  return { data, layout };
}

// The figure on the page, in the page's theme; redrawn when the theme changes.
function Plot({ figure, spec, onReady, description = "Graph of the selected runs. The table below has the same numbers." }) {
  const node = useRef(null);
  const [plotly, setPlotly] = useState(null);
  const [theme, setTheme] = useState(0);

  useEffect(() => {
    import("plotly.js-dist-min").then((m) => setPlotly(m.default || m));
    const redraw = () => setTheme((t) => t + 1);
    const watcher = new MutationObserver(redraw);
    watcher.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    media?.addEventListener("change", redraw);
    return () => { watcher.disconnect(); media?.removeEventListener("change", redraw); };
  }, []);

  useEffect(() => {
    if (!plotly || !node.current || !figure) return;
    const css = getComputedStyle(document.documentElement);
    const { data, layout } = themed(figure, (name) => css.getPropertyValue(name).trim());
    // Keep the viewer's zoom while styling; a new scale, axis or set of panels starts fresh.
    layout.uirevision = [spec.datasets.join(","), spec.metrics.join(","), spec.x, spec.type, spec.layout, spec.x_scale, spec.y_scale,
      spec.baseline || "", spec.difference || "", spec.relative_threshold, JSON.stringify(spec.histogram), JSON.stringify(spec.sweep),
      JSON.stringify(spec.range)].join("|");
    plotly.react(node.current, data, layout, PLOT_CONFIG);
    onReady({ plotly, node: node.current });
  }, [plotly, figure, theme]);

  useEffect(() => () => { if (plotly && node.current) plotly.purge(node.current); }, [plotly]);
  return <div ref={node} className="plot" role="img" aria-label={description} />;
}

function Segmented({ label, value, options, onChange, disabled }) {
  return (
    <div className="inline-seg">
      <span className="field-label">{label}</span>
      <div className="sb-tabs" role="group" aria-label={label}>
        {options.map(([id, text]) => (
          <button key={id} className={value === id ? "active" : ""} disabled={disabled} aria-pressed={value === id}
            onClick={() => value !== id && onChange(id)}>{text}</button>
        ))}
      </div>
    </div>
  );
}

function RangeControl({ range, x, source, onApply, onClear, disabled }) {
  const [from, setFrom] = useState(range?.from ?? "");
  const [to, setTo] = useState(range?.to ?? "");
  useEffect(() => { setFrom(range?.from ?? ""); setTo(range?.to ?? ""); }, [range?.from, range?.to]);
  const ok = from !== "" && to !== "" && Number(from) < Number(to);
  return (
    <div className="range-control">
      <span className="field-label">{source ? "Source range of" : "Range of"} {x === "step" ? "steps" : x}</span>
      <input type="number" value={from} disabled={disabled} onChange={(e) => setFrom(e.target.value)} aria-label="From" placeholder="from" />
      <input type="number" value={to} disabled={disabled} onChange={(e) => setTo(e.target.value)} aria-label="To" placeholder="to" />
      <button disabled={disabled || !ok} onClick={() => onApply(Number(from), Number(to))}>Apply</button>
      {range && <button className="link" disabled={disabled} onClick={onClear}>Show all</button>}
    </div>
  );
}

function TextSetting({ label, value, max, onCommit, disabled, small }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => { if (text.trim() !== (value || "")) onCommit(text.trim()); };
  return (
    <label className={`inline ${small ? "small-setting" : "text-setting"}`}>{label}
      <input value={text} maxLength={max} disabled={disabled} onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); }} />
    </label>
  );
}

function NoteControl({ annotations, onAdd, onRemove, disabled }) {
  const [x, setX] = useState("");
  const [text, setText] = useState("");
  return (
    <div className="note-control">
      <div className="control-row wrap">
        <label className="inline">Note at x<input type="number" value={x} disabled={disabled} onChange={(e) => setX(e.target.value)} /></label>
        <label className="inline text-setting">Text<input value={text} maxLength={120} disabled={disabled} onChange={(e) => setText(e.target.value)} /></label>
        <button disabled={disabled || x === "" || !text.trim()} onClick={() => { onAdd(Number(x), text.trim()); setText(""); }}>Add note</button>
      </div>
      {annotations.length > 0 && (
        <ul className="note-chips">
          {annotations.map((a, i) => (
            <li key={i}><span className="mono">{formatValue(a.x)}</span> {a.text}
              <button className="icon-btn" aria-label={`Remove note ${i + 1}`} disabled={disabled} onClick={() => onRemove(i)}><X /></button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NumberSetting({ label, value, min, max, step = "any", disabled, onCommit }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const number = Number(text);
  const valid = text !== "" && Number.isFinite(number) && (min === undefined || number >= min)
    && (max === undefined || number <= max) && (step !== 1 || Number.isInteger(number));
  return <form className="viz-number-setting" onSubmit={(e) => { e.preventDefault(); if (valid) onCommit(number); }}>
    <label className="inline">{label}<input type="number" value={text} min={min} max={max} step={step} required disabled={disabled}
      onChange={(e) => setText(e.target.value)} /></label>
    <button type="submit" aria-label={`Apply ${label.toLowerCase()}`} disabled={disabled || !valid || number === Number(value)}>Apply</button>
  </form>;
}

function SweepControl({ parameter, at, parameters, disabled, onApply }) {
  const [picked, setPicked] = useState(parameter || parameters[0] || "");
  const [mode, setMode] = useState(at === null ? "final" : "step");
  const [step, setStep] = useState(at ?? "");
  useEffect(() => { setPicked(parameter || parameters[0] || ""); }, [parameter, parameters.join("|")]);
  useEffect(() => { setMode(at === null ? "final" : "step"); setStep(at ?? ""); }, [at]);
  const valid = picked && (mode === "final" || (step !== "" && Number.isFinite(Number(step))));
  return <form className="viz-special-controls" onSubmit={(e) => { e.preventDefault(); if (valid) onApply(picked, mode === "final" ? null : Number(step)); }}>
    <div className="control-row wrap">
      <label className="inline">Experiment parameter
        <select value={picked} disabled={disabled || !parameters.length} onChange={(e) => setPicked(e.target.value)}>
          {!parameters.length && <option value="">No numeric experiment parameters</option>}
          {parameters.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>
      <label className="inline">Value at
        <select value={mode} disabled={disabled} onChange={(e) => setMode(e.target.value)}>
          <option value="final">Final recorded step</option><option value="step">A chosen step</option>
        </select>
      </label>
      {mode === "step" && <label className="inline">Step<input type="number" step="any" required value={step} disabled={disabled}
        onChange={(e) => setStep(e.target.value)} /></label>}
      <button disabled={disabled || !valid} type="submit">Apply sweep</button>
    </div>
    <p className="muted xs">Pick experiments with a numeric parameter to compare their final values or values at a chosen step. Estimates between measured points are marked in the notes.</p>
  </form>;
}

function PublicationExport({ spec, token, disabled }) {
  const [settings, setSettings] = useState({ format: "pdf", width: 6.4, height: 4.8, dpi: 300, font_size: 10, font_family: "DejaVu Sans" });
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const update = (key, value) => setSettings((current) => ({ ...current, [key]: value }));
  async function download(event) {
    event.preventDefault();
    setWorking(true); setError("");
    try {
      const response = await fetch("/api/visualize/export", { method: "POST", headers: {
        "Content-Type": "application/json", Authorization: `Bearer ${token}`,
      }, body: JSON.stringify({ spec, ...settings, width: Number(settings.width), height: Number(settings.height),
        dpi: Number(settings.dpi), font_size: Number(settings.font_size) }) });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(typeof detail?.detail === "string" ? detail.detail : `The publication file could not be exported (HTTP ${response.status}).`);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `${(spec.style.title || "expandrg-graph").replace(/[^\w.-]+/g, "_")}.${settings.format}`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError(e.message); }
    finally { setWorking(false); }
  }
  return <details className="more-controls viz-publication-export">
    <summary>Publication export</summary>
    <p className="muted small">Choose print dimensions, resolution, and type size. Exports use a white background and the current graph settings.</p>
    <form onSubmit={download}>
      <div className="viz-export-grid">
        <label>File format<select value={settings.format} disabled={working} onChange={(e) => update("format", e.target.value)}>
          <option value="pdf">PDF</option><option value="svg">SVG</option><option value="png">PNG</option>
        </select></label>
        <label>Width (inches)<input type="number" min="2" max="20" step="0.1" required value={settings.width} disabled={working}
          onChange={(e) => update("width", e.target.value)} /></label>
        <label>Height (inches)<input type="number" min="2" max="20" step="0.1" required value={settings.height} disabled={working}
          onChange={(e) => update("height", e.target.value)} /></label>
        <label>Resolution (DPI)<input type="number" min="72" max="600" step="1" required value={settings.dpi} disabled={working}
          onChange={(e) => update("dpi", e.target.value)} /></label>
        <label>Font size (points)<input type="number" min="6" max="28" step="0.5" required value={settings.font_size} disabled={working}
          onChange={(e) => update("font_size", e.target.value)} /></label>
        <label>Font family<select value={settings.font_family} disabled={working} onChange={(e) => update("font_family", e.target.value)}>
          <option>DejaVu Sans</option><option>DejaVu Serif</option><option>Liberation Sans</option>
        </select></label>
      </div>
      <button type="submit" disabled={disabled || working}><Download /> {working ? "Preparing file…" : "Export publication file"}</button>
      <ErrorNote error={error} />
    </form>
  </details>;
}

function ClaimChecks({ checks }) {
  const notes = (checks || []).filter((c) => c.status === "mismatch" || c.status === "unverified");
  if (!notes.length) return null;
  return <div className="viz-claim-checks warning-note" role="note">
    <strong>Agent claim checks</strong>
    <ul>{notes.map((check, index) => <li key={index}>
      {check.claim && <span>“{check.claim}” — </span>}{check.message || "This claim could not be verified from the graph's data."}
    </li>)}</ul>
  </div>;
}

function SaveButton({ current, spec, onSave, disabled }) {
  const [naming, setNaming] = useState(false);
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  if (current) return <span className="saved-badge" title="Changes are saved as you make them">Saved</span>;
  if (!naming) {
    return <button onClick={() => { setTitle(spec.style.title || ""); setNaming(true); }} disabled={disabled || !spec.datasets.length}><Save /> Save</button>;
  }
  return (
    <form className="save-form" onSubmit={async (e) => {
      e.preventDefault(); if (!title.trim() || saving) return;
      setSaving(true); setError("");
      try { await onSave(title.trim()); setNaming(false); } catch (e) { setError(e.message); }
      finally { setSaving(false); }
    }}>
      <input autoFocus value={title} maxLength={120} disabled={disabled || saving} placeholder="Name this graph" onChange={(e) => setTitle(e.target.value)} />
      <button className="primary" type="submit" disabled={disabled || saving || !title.trim()}>Save</button>
      <button type="button" className="link" disabled={saving} onClick={() => setNaming(false)}>Cancel</button>
      <ErrorNote error={error} />
    </form>
  );
}

// The same numbers as the graph, readable without hovering (from every point, never a thinned copy).
function StatsTable({ rows, x }) {
  const at = x === "step" ? "step" : x;
  return (
    <details className="stats-table" open={rows.length <= 4}>
      <summary>Numbers ({rows.length} series)</summary>
      <div className="table-scroll">
      <table className="numbers-table">
        <thead>
          <tr><th>Run</th><th>Measure</th><th>Points</th><th>Lowest</th><th>Highest</th><th>Last</th><th>First NaN/Inf</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.dataset}|${r.metric}`}>
              <td>{r.label}</td>
              <td>{r.metric}{r.derived === "difference" && <span className="viz-series-detail muted xs">Derived difference</span>}
                {r.parameter && <span className="viz-series-detail muted xs">{r.parameter} · {r.at_step === null || r.at_step === undefined ? "final step" : `step ${formatValue(r.at_step)}`}{r.interpolated ? " · estimated" : ""}</span>}</td>
              <td>{r.points}{r.from !== null ? ` (${at} ${formatValue(r.from)}–${formatValue(r.to)})` : ""}</td>
              <td>{r.min === null ? "–" : `${formatValue(r.min)} at ${formatValue(r.min_at)}`}</td>
              <td>{r.max === null ? "–" : `${formatValue(r.max)} at ${formatValue(r.max_at)}`}</td>
              <td>{r.last === null ? "–" : `${formatValue(r.last)} at ${formatValue(r.last_at)}`}</td>
              <td>{r.first_gap_at === null ? "none" : `${at} ${formatValue(r.first_gap_at)}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </details>
  );
}

// Edit with Agent: the researcher's own agent, about this graph. Its operations are checked like any
// others; what it couldn't change is listed under its reply.
function GraphAgent({ session, spec, current, focus, ensureSaved, onResult, onBusyChange }) {
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(null); // { jobId, startedAt, question }
  const [sending, setSending] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const input = useRef(null);
  const scroller = useRef(null);
  const exchanges = current?.conversation || [];

  useEffect(() => { if (focus) input.current?.focus({ preventScroll: true }); }, [focus]);
  useEffect(() => { onBusyChange(!!pending || sending); return () => onBusyChange(false); }, [pending, sending, onBusyChange]);
  useEffect(() => { if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight; }, [exchanges.length, pending]);
  useEffect(() => {
    if (!pending) return undefined;
    let stopped = false;
    let timer;
    const clock = setInterval(() => setElapsed(Math.floor((Date.now() - pending.startedAt) / 1000)), 1000);
    async function poll() {
      try {
        const job = await backend(`/jobs/${pending.jobId}`, { token: session.token });
        if (stopped) return;
        if (job.status === "running") { timer = setTimeout(poll, 1500); return; }
        if (job.status === "failed") setError(job.error?.message || "Your agent couldn't reply. Try again.");
        else onResult(job.diagnosis);
        setPending(null);
      } catch (e) {
        if (!stopped) { setError(e.message); setPending(null); }
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); clearInterval(clock); };
  }, [pending]);

  async function send(event) {
    event.preventDefault();
    const question = message.trim();
    if (!question || pending || sending) return;
    setError("");
    setSending(true);
    try {
      const graph = await ensureSaved();
      const started = await backend(`/visualizations/${graph.id}/agent`, { method: "POST", token: session.token, body: { message: question } });
      setMessage("");
      setElapsed(0);
      setPending({ jobId: started.job_id, startedAt: Date.now(), question });
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="agent-chat graph-agent" aria-label="Edit with Agent">
      <div className="agent-chat-head">
        <span className="li-icon accent"><BrainCircuit /></span>
        <div className="agent-chat-title">
          <strong>Edit with Agent</strong>
          <span className="muted xs">Your own agent, about this graph</span>
        </div>
      </div>
      <div className="agent-transcript" ref={scroller} role="log" aria-live="polite">
        {!exchanges.length && !pending && (
          <p className="chat-empty muted small">
            Ask in plain words: "use steps 400 to 900", "show residual instead", "add the last experiment", "use a log scale",
            or "these results look wrong". It changes the graph only through checked operations, and looks into the data
            instead of changing it.
          </p>
        )}
        {exchanges.map((x, i) => (
          <article className="exchange" key={i}>
            <div className="msg msg-me"><p className="msg-box question">{x.question}</p></div>
            <div className="msg msg-ai">
              <div className="msg-col">
                <div className="msg-box agent-reply"><Markdown text={x.reply} /></div>
                <ClaimChecks checks={x.claim_checks} />
                {(x.applied?.length > 0 || x.refused?.length > 0) && (
                  <div className="msg-src">
                    {x.applied?.map((o, j) => <span key={`a${j}`} className="src-chip">changed: {describe(o)}</span>)}
                    {x.refused?.map((r, j) => <span key={`r${j}`} className="src-chip refused" title={r.reason}>not applied: {describe(r.op)}</span>)}
                  </div>
                )}
              </div>
            </div>
          </article>
        ))}
        {pending && (
          <div className="exchange">
            <div className="msg msg-me"><p className="msg-box question">{pending.question}</p></div>
            <div className="msg msg-ai"><p className="msg-box muted" role="status"><span className="dot pulse" /> Working on it… {elapsed}s</p></div>
          </div>
        )}
      </div>
      <div className="agent-feedback"><ErrorNote error={error} /></div>
      <form className="ask-dock" onSubmit={send}>
        <div className="ask-row">
          <input ref={input} value={message} maxLength={4000} onChange={(e) => setMessage(e.target.value)} disabled={!!pending || sending}
            placeholder={spec.datasets.length ? "Ask your agent about this graph…" : "Pick a run first, or ask for one…"} aria-label="Message about this graph" />
          <button className="primary send" type="submit" disabled={!!pending || sending || !message.trim()} aria-label="Send">→</button>
        </div>
        <p className="muted xs">The graph is saved first, so the agent's changes can be undone. Each message uses CREAO credits.</p>
      </form>
    </section>
  );
}

function describe(op) {
  if (!op || typeof op !== "object") return "?";
  const rest = Object.entries(op).filter(([k]) => k !== "op").map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") : v}`);
  return `${String(op.op).replace(/_/g, " ")}${rest.length ? ` (${rest.join("; ")})` : ""}`;
}

// A diagnosis and its draft retain private saved graphs; opening one returns to the Studio.
export function DiagnosticGraphs({ graphs, session, onOpen }) {
  if (!graphs?.length) return null;
  return <div className="viz-diagnostics">
    <h4>Diagnostic graphs</h4>
    <p className="muted small">Saved privately with this investigation. Open a graph to inspect its data and edit its settings.</p>
    {graphs.map((graph) => <DiagnosticPreview key={graph.id} graph={graph} token={session.token} onOpen={onOpen} />)}
  </div>;
}

function DiagnosticPreview({ graph, token, onOpen }) {
  const [loaded, setLoaded] = useState(null);
  const [error, setError] = useState("");
  const revision = JSON.stringify(graph.spec || {});
  useEffect(() => {
    let stopped = false;
    setLoaded(null); setError("");
    backend(`/visualizations/${encodeURIComponent(graph.id)}`, { token })
      .then((value) => { if (!stopped) setLoaded(value); })
      .catch((e) => { if (!stopped) setError(e.message); });
    return () => { stopped = true; };
  }, [graph.id, revision, token]);
  return <section className="viz-diagnostic-preview">
    <div className="control-row wrap"><strong>{graph.title}</strong>
      {onOpen && <button type="button" onClick={() => onOpen(graph)}>Open diagnostic graph</button>}
    </div>
    <ErrorNote error={error} />
    {loaded?.figure ? <Plot figure={loaded.figure} spec={loaded.spec} onReady={() => {}} description="Diagnostic graph. Open diagnostic graph to inspect its data and settings." />
      : !error && <p className="muted small">Loading diagnostic graph…</p>}
    {loaded?.notes?.length > 0 && <ul className="graph-notes">{loaded.notes.map((note) => <li key={note}>{note}</li>)}</ul>}
  </section>;
}
