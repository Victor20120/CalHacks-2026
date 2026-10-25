# Visualization Studio: design

**Status:** Phases 1 and 2 built (build plan steps 9–10, the dashboard’s **Visualize** page). The implementation and verification notes are in [CHANGELOG.md](../CHANGELOG.md). Phase 3 remains future work.

**Goal:** researchers compare simulation runs on publication-quality, interactive graphs. They change graphs by hand or by asking their own CREAO agent ("use run 3 as the baseline", "only steps 400 to 900"). Graphs can be saved, reopened and exported.

**Principle:** CREAO decides *what* to show. A deterministic toolkit in ExpandRG does every calculation and draws every graph. The agent never draws, never writes plotting code, and never changes a number.

---

## 1. What exists, and what this reuses

| Need | Already in ExpandRG | Reuse |
|---|---|---|
| Run data | `simulation_runs`, `run_points`, `run_alerts` in the shared database (`backend/monitoring/runs.py`). Numbers per step for up to 6 measures per run, read from the log. NaN/Inf are stored as gaps. Past 1,000 points, every other point is dropped | Source A: watched runs, with the existing permission checks (`visible_runs`, `get_visible_run`) |
| Parameters | Experiments (`local_connector/experiments/`): each record keeps the entry point, the changed parameters and its log, and `summarize()` turns the log into numbers per step. The original run's numbers come with it | Source B: experiments, with their parameters |
| Graphs | `frontend/src/charts.jsx`: SVG line charts per measure (log scale for blow-ups, alert markers, hover, replay) and `CompareCharts` (an original run plus up to 3 copies) | Kept on their current pages. The Studio uses Plotly, and each existing chart gets **Edit with Agent** to open in the Studio |
| Agent asks ExpandRG to act | The personal agent ends a reply with ` ```json {"tool_requests": [...]}``` ` or ` {"share": ...} `; ExpandRG checks each request and runs it with the researcher's permissions, at most 2 rounds per message (`backend/orchestration/toolkit.py`, `personal_chat.py`, `extract_json_block`) | A new block, ` {"graph_ops": [...]} `, checked the same way. The agent can still use its tools first to investigate |
| Sending data to CREAO | `get_simulation_runs` sends a measure as its range, its first non-finite step and at most 40 evenly spaced points (`_series`), never the whole series | The same summaries describe each graph's data to the agent |
| Long-running agent work | `JobStore` and `run_job`, polled by the page | Agent edits run as jobs |
| Private storage | Each researcher's private database (`backend/memory/private_memory.py`) | Saved graphs and their history go here. New tables need no schema version bump |
| UI | The console shell, cards, segmented tabs, the chat panel (`AgentChat.jsx`), `FolderPicker` | The Studio is one more page in the same app |
| Investigations | Diagnoses, drafts and `finding_runs`; a Super Brain update already shows its run's graph | Phase 2: diagnostic graphs attach to a diagnosis and travel with the draft |

### What the data does not have (and so the Studio must not invent)

- **Units.** Measures are names from the log (`max|T|`, `residual`). Axes show the name. A unit appears only when the researcher sets it, or when the project's `expandrg.json` declares it (Phase 2). The agent cannot set units.
- **Physical time.** Runs are indexed by step. Time (or another independent variable, such as beats) can be the x axis only when the run logs it as a measure, and only after it's checked to increase with step.
- **Parameters for watched runs.** Only experiments carry them. A watched run shows its label and nothing else.
- **Full resolution for long watched runs.** They are thinned past 1,000 points. The Studio says so on the series ("every other point kept after step N"). Reading the original log through the connector is Phase 2.
- **Uncertainty, fields and trajectories.** There are no error estimates and no 2D or 3D field data. Error bars, heatmaps of fields and 3D trajectories wait until a data source provides them (Phase 3). A parameter-sweep heatmap is possible from experiments (Phase 2).

---

## 2. The smallest set of changes

**Backend: one new package, `backend/visualization/`**, pure functions with no I/O except in `sources.py`:

| Module | Does |
|---|---|
| `spec.py` | The **graph spec**, a versioned JSON document and the single source of truth for a graph. It holds the datasets and measures shown, the x axis, graph type, overlay or separate panels, baseline, range, axis scales, annotations, style, researcher-set units, and its history |
| `sources.py` | Loads datasets for a viewer: watched runs from the database, experiments from the connector. Every series comes back with flags: thinned, gap (non-finite) steps, and source (original or experiment copy) |
| `transforms.py` | The numerics: range filter, x axis from a measure (checked to be increasing), alignment onto a common grid (linear interpolation, inside the overlap only, never across a gap, never extrapolated), differences against a baseline, summary statistics, and display downsampling that keeps the minimum and maximum of each bucket so spikes survive |
| `figure.py` | Turns spec plus transformed data into Plotly figure JSON: traces, axis titles (with units only when known), legend, alert markers, annotations. Derived and interpolated series get a distinct style and a legend note |
| `ops.py` | The operation vocabulary (section 3): each operation's schema, its validation against the current spec and catalog, and `apply(spec, op)`, which returns a new spec or a refusal with its reason |

**API** (added to `backend/api/app.py`; all need sign-in and project membership):

| Endpoint | Purpose |
|---|---|
| `GET /visualize/datasets?project_id=` | The catalog: the viewer's watched runs and experiments in the project, with label, date, measures, step range, parameters (experiments), alert counts and flags |
| `POST /visualize/figure` | Body: a spec. Returns the normalized spec, the Plotly figure, notes (alignment, interpolation, thinning, hidden alerts) and statistics. Deterministic, never calls CREAO |
| `POST /visualize/ops` | Body: a spec and operations. Validates and applies them, then returns as above. The page's own controls use this too, so manual and agent edits share one path |
| `GET`, `POST`, `PUT`, `DELETE /visualizations[/{id}]` | Saved graphs in the researcher's private database |
| `POST /visualizations/{id}/agent` | Body: `message`. Starts an Edit with Agent job (poll `/jobs/{id}`). The result is the agent's reply, the operations applied and refused, and the new spec |

**Storage** (private database, new tables):
- `visualizations`: `id`, `project_id`, `title`, `spec` (JSON), `conversation` (JSON, the graph's own chat), `created_at`, `updated_at`.
- `visualization_history`: `id`, `visualization_id`, `at`, `actor` (`researcher` or `agent`), `ops` (JSON), `creao_run_id`.

Datasets are referenced, never copied. Each series in a saved spec keeps a fingerprint (point count, last step, a hash of its values). Reopening a graph whose source has changed, for example a run that kept going, says so.

**Frontend:**
- New researcher page **Visualize** in the Simulation group, `frontend/src/views/VisualizeView.jsx`, with three panels:
  - **Runs:** the catalog with checkboxes, parameters, a baseline choice and filters.
  - **Graph:** Plotly with zoom, pan, hover values, legend toggles, overlay or separate panels, and controls for measure, x axis, range, scale and graph type.
  - **Agent:** the graph's chat, built on the `AgentChat` pattern, with **Edit with Agent** focusing it.
- Plotly (`plotly.js-dist-min`, pinned) is loaded only when the Studio opens, so other pages don't pay for it.
- **Edit with Agent** buttons on the existing run and experiment charts open the Studio with that graph.

**Unchanged:** the CREAO adapter, the agents, monitoring, the connector's file rules, and every existing page and endpoint.

---

## 3. How CREAO controls the toolkit

### What the agent receives (no full datasets)

1. The current spec.
2. The catalog: dataset IDs, labels, measures, step ranges, parameters and flags.
3. For each series on the graph: its statistics and a 40-point summary (the existing `_series` form). With a baseline, the toolkit's own computed differences (largest absolute and relative difference and where they occur), so explanations cite real numbers.
4. The graph's conversation so far, and the researcher's message.
5. Its existing toolkit (read project files, search the Super Brain, get runs), for investigating before it answers.

### What it returns

Its answer in prose, then ` ```json {"graph_ops": [...]}``` `, or ` {"graph_ops": []} ` when nothing should change. It may first spend its tool rounds investigating, as it does today.

### What ExpandRG does with it

- Every operation is validated before anything changes:
  - only known operations, with only their known arguments;
  - dataset IDs must be in the viewer's catalog, and measures must exist in those datasets;
  - numbers must be finite and ranges ordered;
  - enums and the style keys come from fixed lists.
- **Refused operations:**
  - go back to the agent once, with the reasons, for one corrected try;
  - anything still refused is listed under the reply ("Couldn't apply: run 7 isn't in this project").
- **Applied operations** are recorded in `visualization_history` with actor `agent` and the CREAO run ID. The researcher can undo any step.
- **Edits that hide data are flagged:** when an edit hides a problem, the graph says so (for example "This range hides the Critical alert at step 944"). A range that cuts off an alerted step and removing a run with Critical alerts both count.
- **Nothing runs as code:** operations are data. There are no expressions or formulas, no file paths and no HTML in labels (labels are plain text, length-capped).

### Configuration problem or data problem

The brief tells the agent:
- **A display problem** (wrong axis, wrong measure, range, baseline or style) is fixed with operations.
- **Suspect numbers** ("these results look wrong") are never "fixed": it investigates with its read-only tools (the run's alerts and numbers, the log, the config, the code) and explains. If it finds a real problem, it says so and can propose a diagnosis or draft through the existing flow.

The source data has no write path at all from the Studio, so the original measurements cannot be changed.

### Operations

| Operation | Arguments | Notes |
|---|---|---|
| `create_graph` | `datasets`, `metrics`, optional `x`, `type` | Starts from scratch |
| `select_runs` | `datasets` | Replaces the selection |
| `add_run` / `remove_run` | `dataset` | |
| `change_metric` | `metrics` (one or more) | Must exist in every selected dataset, or the missing ones are named |
| `change_axis` | `x`: `step` or a measure; `y_scale` / `x_scale`: `linear` or `log` | An x measure must increase with step in each dataset; non-positive values become gaps on a log scale, with the count shown |
| `set_time_range` | `from`, `to` (in x units) | Inclusive. Flagged when it hides an alert |
| `set_baseline` | `dataset` | Must be one of the selected datasets |
| `compare_runs` | `datasets`, `mode`: `overlay`, `separate`, `difference` (absolute or relative) | Phase 1: overlay and separate. Phase 2: difference |
| `change_graph_type` | `type`: `line`, `scatter` (measure against measure); Phase 2 adds `histogram`, `sweep` (difference is a comparison layout) (final value against a parameter) | |
| `add_annotation` | `x`, optional `y`, `text` | Plain text, at most 120 characters |
| `update_style` | From a fixed list: `title`, axis labels, `legend` position, `line_width`, `preset` (`screen`, `paper`, `poster`) | No colours by name or free CSS. **Units are not a style:** only the researcher sets them |
| `save_visualization` | `title` | Saves to the researcher's private database |
| `export_figure` | `format`: `png` or `svg` (Phase 1, in the browser); `pdf` (Phase 2) | Prepares the file. The download is the researcher's click |

**Deterministic interactions never call CREAO:** zooming, panning, hovering, legend toggles and every manual control.

---

## 4. Data flow

```
 Researcher ──clicks──▶ Visualize page ──spec + ops──▶ POST /visualize/ops ─▶ ops.py (validate, apply)
     ▲                       ▲                                                   │
     │                       └──────── Plotly figure + notes + stats ◀── figure.py ◀ transforms.py ◀ sources.py
     │                                                                                      │        │
     │                                                                      shared DB (runs)┘        └ connector (experiments, local)
     │
     └─ "use run 3 as the baseline" ─▶ POST /visualizations/{id}/agent (job)
             │
             ├─▶ context: spec, catalog, 40-point summaries, stats, conversation ─▶ researcher's own CREAO agent
             │                                                                        │ (optional tool rounds: runs, files, Super Brain)
             └─◀ prose + {"graph_ops": [...]} ◀────────────────────────────────────────┘
                     │
                     └─▶ ops.py validates ─▶ applied (history: actor=agent, CREAO run) / refused (reasons back once)
                                └─▶ new figure in the page; the conversation continues on this graph
```

- **Saving:** spec, conversation and history go to the private database; the data stays where it is. Reopening rebuilds the figure from the sources and notes any change.
- **During an investigation (Phase 2):**
  1. A Critical alert leads to a diagnosis.
  2. The toolkit makes a default diagnostic graph: the alerted measure around the alert, on a log scale for a blow-up, overlaid on the researcher's last clean run when there is one.
  3. The agent can ask for more graphs in its diagnosis reply, as `graph_ops` for new graphs.
  4. The graphs attach to the diagnosis and its draft.
  5. The researcher opens any of them in the Studio.

---

## 5. Scientific accuracy and reproducibility rules

1. **Nothing is invented:** no values, units, uncertainties or time axes. Missing data is a gap, not a line.
2. **Original and derived look different:** interpolated and derived series (differences, ratios) have a distinct style, a legend note and a tooltip label. Thinned series say so.
3. **Alignment is explicit:** runs are compared on a shared x only within their overlap, with linear interpolation onto the baseline's points (or, with no baseline, the union of steps). Never across a gap or a non-finite value, never extrapolated. The method is stored in the spec and shown under the graph.
4. **Relative differences** only where the baseline magnitude is above a threshold (stored in the spec). Elsewhere they are gaps, counted in the notes.
5. **Log scales** never drop data silently: non-positive points are counted and named.
6. **Downsampling is for display only** and keeps each bucket's minimum and maximum, so a spike or blow-up is never smoothed away. Statistics use all points.
7. **Every graph keeps** its datasets (IDs, labels, parameter snapshots, fingerprints), measures, transforms, alignment method, style and full history.
8. **No writes to source data:** the Studio reads runs and experiments and never changes them.
9. **Permissions:**
   - Only datasets the viewer may see are listed or loaded: a researcher sees their own runs and experiments, as today.
   - The project scope is checked on every call, including the agent's operations.
   - Run labels and log text going to the agent are marked as data, never instructions.

---

## 6. Main risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Misaligned comparisons | Runs have different step spacing, lengths and thinning; naive overlays and differences mislead | Explicit alignment within overlap only, the method shown, and tests on unequal and thinned series |
| Interpolating across a blow-up or gap | Fakes values where the run had none | Never interpolate across non-finite values or gaps; gaps stay gaps |
| Smoothing away the signal | Plain stride sampling can hide the spike that matters | Min–max bucket downsampling for display; statistics on full data |
| The agent picks the wrong measure or run | Similar names (`max|T|`, `T_max`) and run numbers | Catalog IDs, not names, in operations; the applied change is shown; one-click undo |
| The agent "fixes" data | Silently wrong science | No write path to data; the brief separates display from data problems; hiding an alert is flagged |
| The agent claims numbers it never saw | Confident but wrong explanations | It gets the toolkit's computed statistics and differences, and is told to cite them. Referenced numeric claims are checked against the supplied statistics; mismatches and unreferenced numeric prose are marked |
| Injection through labels or logs | Text in data steering the agent | Labels sanitized and length-capped; context marked as data; operations validated whatever the text says |
| Sending research data to CREAO | Privacy and size | Summaries only (≤ 40 points per series plus statistics), as the toolkit already does; the viewer's permissions apply |
| Bundle size | Plotly is about 1 MB gzipped | Loaded only on the Studio page; the existing SVG charts stay elsewhere |
| Cost and latency | Each agent edit is at least one CREAO run (20–60 s) | Only messages call CREAO; all manual interaction is instant and local |
| Missing units and time | Axes look less finished than a paper figure | Honest axis names; units from the researcher (Phase 1) or the manifest (Phase 2); a time axis only from a logged measure |

---

## 7. Phases

**Phase 1: essential** (build plan step 9)
- Catalog of watched runs and experiments.
- Plotly line graphs: one or more runs, one or more measures, overlay or separate panels, step or a logged measure on x, linear or log scales, a range, and a scatter of measure against measure.
- Manual controls through `POST /visualize/ops`.
- Edit with Agent with the Phase 1 operations.
- Save, reopen and undo.
- PNG and SVG export from the browser.
- Edit with Agent on the existing run and experiment charts.

Built as planned, with these details settled while building: exports are always drawn light on white; with several runs in one panel the alert lines are listed in the notes instead of labelled (the labels collided); labels at line ends are left to the legend when a value is near the float limit (Plotly can't place them); and runs with no numbers per step aren't in the catalog.

**Phase 2: research analysis** (build plan step 10)
- Baseline differences (absolute and relative) and difference plots.
- Parameter-sweep plots from experiments.
- Histograms.
- Units in `expandrg.json`.
- Full-resolution data from the run's log through the connector.
- Diagnostic graphs made during investigations and attached to diagnoses and drafts.
- Publication export (PDF, SVG and PNG at set sizes and fonts) with Matplotlib, as an optional extra so the base install stays small.
- Checking the numbers the agent quotes against the statistics.

Phase 2 is implemented with these boundaries: full-log reads are explicit researcher actions, limited to 100 MB/one million metric points, and recorded samples must still match the source; unavailable or changed logs retain the recorded data. Project manifest units are defaults, while explicit graph units win. Relative differences at or below the saved baseline threshold are gaps with counts. Histograms use common deterministic bin edges, and sweeps show final values or gap-aware interpolation at a chosen step without extrapolation. Matplotlib export renders the checked browser traces with plain-text labels. Diagnostic graphs are private attachments to the diagnosis’s existing episode and draft; sharing graphs remains Phase 3.

**Phase 3: later**
- 3D and field visualization (PyVista) once runs produce field data.
- Reusable templates across projects.
- Sweep dashboards.
- Sharing a saved graph with the group, attached to a finding.

---

## 8. Decisions (taken 2026-10-24)

1. **Plotly in the browser:** 1.4 MB gzipped (`plotly.js-dist-min` 2.35.2), loaded only by the Studio, since zoom, pan, legend toggles and PNG/SVG export come built in.
2. **Matplotlib for Phase 2 exports**, as an optional extra (`expandrg[export]`): PDF export needs it, and kaleido needs a Chrome install.
3. **Leaders:** they have no personal agent, so they build graphs by hand from the runs they can see, and Edit with Agent is for researchers. Later, a leader's requests could go to the Super Brain hub agent.
4. **Where units come from:** the researcher per graph (Phase 1), then `expandrg.json` (`"units": {"max|T|": "K"}`) in Phase 2. The agent never sets them.
