"""The Plotly figure for a spec, built from the datasets' own numbers.

Colours come from a validated categorical palette (checked for colour-vision deficiency and on
ExpandRG's light and dark surfaces): a run keeps its slot (spec["colors"]), and the page swaps
in the dark steps by slot. Each measure gets its own panel, never a second y axis. With two to
four runs, each line is also labelled at its end; there is always a legend for two or more, and
the statistics table carries the same numbers, so colour and hover are never the only way to
read a graph. Text is escaped: Plotly reads a little HTML in labels.
"""
from __future__ import annotations

import html
import math

from backend.visualization.sources import fingerprint
from backend.visualization.spec import MAX_PANELS, SpecError
from backend.visualization.transforms import (
    align, compare, difference, downsample, for_log, histogram, histogram_edges, in_range, increasing, stats, xy,
)

PALETTE_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
PALETTE_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767")
STATUS = {"critical": "#d03b3b", "warning": "#fab219"}
PRESETS = {"screen": (12, 1.0), "paper": (11, 1.0), "poster": (18, 1.5)}  # font size, line width factor
PANEL_HEIGHT = 260
DIRECT_LABELS_UP_TO = 4
HUGE = 1e250  # Plotly can't place text near the float limit on a linear axis (a blown-up run); the legend names it


def build(spec: dict, datasets: dict[str, dict]) -> dict:
    """{"figure", "notes", "stats", "comparisons", "fingerprints", "metrics", "points"}. points holds
    each shown series in range, for the agent's summaries; it isn't sent to the page."""
    if spec["type"] == "histogram":
        return _histogram(spec, datasets)
    if spec["type"] == "sweep":
        return _sweep(spec, datasets)
    notes: list[str] = []
    shown = [d for d in spec["datasets"] if d in datasets]
    metrics = spec["metrics"] or _first_metric(shown, datasets)
    derived = spec["layout"] == "difference"
    baseline = spec.get("baseline")
    overlay = spec["layout"] != "separate"
    drawn_runs = [d for d in shown if d != baseline] if derived else shown
    panels = [(None, m) for m in metrics] if overlay else [(d, m) for d in shown for m in metrics]
    if len(panels) > MAX_PANELS:
        raise SpecError(f"Separate panels would make {len(panels)} graphs; at most {MAX_PANELS}. Show fewer runs or measures.")
    font, widen = PRESETS[spec["style"]["preset"]]
    width = spec["style"]["line_width"] * widen
    x = spec["x"]
    labels_at_ends = overlay and 2 <= len(drawn_runs) <= DIRECT_LABELS_UP_TO
    traces, shapes, annotations, table, points, prints = [], [], [], [], {}, {}
    legend_shown: set[str] = set()
    marked: dict[str, list[str]] = {}  # run label -> its unlabelled alert lines, said once in the notes
    count = max(1, len(panels))
    layout: dict = {}

    for i, (only, metric) in enumerate(panels):
        axis = "" if i == 0 else str(i + 1)
        top = 1 - i / count
        bottom = top - 1 / count + (0.07 if i < count - 1 else 0)
        layout[f"xaxis{axis}"] = {
            "domain": [0, 1], "anchor": f"y{axis}", "type": spec["x_scale"], "showticklabels": i == count - 1,
            "zeroline": False, "showline": True, "ticks": "outside", "exponentformat": "power", "automargin": True,
            **({"matches": "x"} if i else {}),
            **({"title": {"text": _esc(_x_title(spec))}} if i == count - 1 else {}),
        }
        layout[f"yaxis{axis}"] = {
            "domain": [max(0, bottom), top - (0.04 if count > 1 else 0)], "anchor": f"x{axis}", "type": spec["y_scale"],
            "zeroline": False, "showline": True, "ticks": "outside", "exponentformat": "power", "automargin": True,
            "title": {"text": _esc(_y_title(spec, metric, len(metrics)))},
        }
        if count > 1:
            title = metric if only is None else f"{datasets[only]['label']} · {metric}"
            annotations.append({"text": _esc(title), "xref": "paper", "yref": "paper", "x": 0, "y": top - 0.035,
                                "xanchor": "left", "yanchor": "bottom", "showarrow": False, "meta": "panel-title",
                                "font": {"size": font}})

        if derived and baseline in datasets and x == "step":
            found = _alert_markers(datasets[baseline], metric, axis, spec["range"], font, shapes, annotations, notes,
                                   labelled=False, x_scale=spec["x_scale"])
            if found:
                marked.setdefault(datasets[baseline]["label"], found)

        for d in [only] if only else drawn_runs:
            data = datasets[d]
            raw = xy(data["series"], metric, x)
            if raw is None:
                missing = metric if metric not in data["series"] else x
                notes.append(f"{data['label']} has no {missing}, so it isn't in the {metric} graph.")
                continue
            if spec["type"] == "line" and x != "step" and not increasing(raw):
                notes.append(f"{data['label']}: {x} doesn't increase with step, so it can't be the x axis of a line graph. "
                             "A scatter can show it.")
                continue
            source = raw
            label = data["label"]
            if derived:
                reference = datasets.get(baseline)
                base_points = xy(reference["series"], metric, x) if reference else None
                if base_points is None:
                    notes.append(f"The baseline has no {metric} against {x}; its difference cannot be drawn.")
                    continue
                if not increasing(base_points) or not increasing(raw):
                    notes.append(f"{label}: differences need an increasing x axis in both runs.")
                    continue
                result = difference(in_range(base_points, spec["range"]), raw,
                                    spec["difference"] == "relative", spec["relative_threshold"])
                raw = result["points"]
                label = f"{label} − {reference['label']}"
                prints[f"{baseline}|{metric}"] = fingerprint(base_points)
                if result["small_baseline"]:
                    notes.append(f"{label}: {result['small_baseline']} relative differences are gaps because the baseline's "
                                 f"absolute value is at or below {spec['relative_threshold']:g}.")
                if result["overflowed"]:
                    notes.append(f"{label}: {result['overflowed']} differences exceed the numeric range and remain gaps.")
                if result["interpolated"]:
                    notes.append(f"{label}: differences use straight-line estimates within the overlap at unmeasured x values; "
                                 "no gaps are crossed and no values are extrapolated.")
                if not raw:
                    notes.append(f"{label}: these runs have no overlapping x values in this range.")
            kept = in_range(raw, spec["range"])
            points[f"{d}|{metric}"] = kept
            prints[f"{d}|{metric}"] = fingerprint(source)
            table.append({"dataset": d, "label": label, "metric": metric, **stats(kept),
                          **({"derived": "difference", "baseline": baseline, "difference": spec["difference"]} if derived else {})})
            if metric in data.get("thinned", []):
                notes.append(f"{data['label']}: past 1,000 points the monitor kept every other {metric} value, "
                             "so single steps may be missing (the shape and every NaN/Inf are kept).")
            draw = kept
            if spec["y_scale"] == "log":
                draw, hidden = for_log(draw)
                if hidden:
                    notes.append(f"{data['label']}: {hidden} {metric} value{'s' if hidden != 1 else ''} at or below zero "
                                 "can't be shown on a log scale.")
            if spec["x_scale"] == "log":
                dropped = sum(1 for p in draw if p[0] <= 0)
                draw = [p for p in draw if p[0] > 0]
                if dropped:
                    notes.append(f"{data['label']}: {dropped} point{'s' if dropped != 1 else ''} at x ≤ 0 can't be shown on a log x axis.")
            draw = downsample(draw)
            slot = spec["colors"].get(d, 0)
            color = PALETTE_LIGHT[slot]
            trace = {
                "type": "scatter", "mode": "lines" if spec["type"] == "line" else "markers",
                "x": [p[0] for p in draw], "y": [p[1] for p in draw], "connectgaps": False,
                "name": _esc(label), "legendgroup": d, "showlegend": d not in legend_shown,
                "xaxis": f"x{axis}", "yaxis": f"y{axis}", "meta": {"slot": slot, "dataset": d, "metric": metric,
                                                                              **({"derived": "difference", "baseline": baseline} if derived else {})},
                "hovertemplate": f"{_esc(label)}: %{{y:.6g}}<extra></extra>",
            }
            if spec["type"] == "line":
                trace["line"] = {"color": color, "width": width, **({"dash": "dash"} if derived else {})}
            else:
                trace["marker"] = {"color": color, "size": 8, "line": {"width": 2, "color": "#ffffff"}}
            legend_shown.add(d)
            traces.append(trace)
            end = next((p for p in reversed(draw) if p[1] is not None), None)
            if labels_at_ends and end is not None and (spec["y_scale"] == "log" or abs(end[1]) < HUGE):
                annotations.append({
                    "text": _esc(_short(label)), "x": _axis_pos(end[0], spec["x_scale"]),
                    "y": _axis_pos(end[1], spec["y_scale"]), "xref": f"x{axis}", "yref": f"y{axis}",
                    "xanchor": "left", "xshift": 6, "showarrow": False, "meta": "direct-label", "font": {"size": font - 1}})
            if x == "step":
                found = _alert_markers(data, metric, axis, spec["range"], font, shapes, annotations, notes,
                                       labelled=only is not None or len(shown) == 1, x_scale=spec["x_scale"])
                if found and only is None and len(shown) > 1:
                    marked.setdefault(data["label"], found)

    for a in spec["annotations"]:
        annotations.append({"text": _esc(a["text"]), "x": _axis_pos(a["x"], spec["x_scale"]), "xref": "x",
                            **({"y": _axis_pos(a["y"], spec["y_scale"]), "yref": "y", "showarrow": True, "arrowhead": 2}
                               if a.get("y") is not None else {"y": 1, "yref": "y domain", "yanchor": "bottom", "showarrow": False}),
                            "meta": "note", "font": {"size": font - 1}})

    comparisons = []
    if len(shown) >= 2 and not derived:
        for metric in metrics:
            base = points.get(f"{shown[0]}|{metric}")
            for other in shown[1:]:
                theirs = points.get(f"{other}|{metric}")
                result = compare(base, theirs) if base and theirs else None
                if result:
                    comparisons.append({"metric": metric, "base": datasets[shown[0]]["label"],
                                        "other": datasets[other]["label"], **result})
                    if result["overflowed"]:
                        notes.append(f"{metric}: differences between {datasets[shown[0]]['label']} and "
                                     f"{datasets[other]['label']} exceed the numeric range at {result['overflowed']} "
                                     "points, so their largest difference cannot be reported as a finite number.")

    if marked:
        notes.append("Red lines mark each run's first Critical alert, amber its first Warning: "
                     + "; ".join(f"{label}, {', '.join(found)}" for label, found in marked.items()) + ".")
    if not shown:
        notes.append("Pick at least one run to graph.")
    if derived:
        notes.append("Derived differences are target − baseline" +
                     (", divided by the baseline for relative differences (dimensionless)." if spec["difference"] == "relative" else ".") +
                     " Dashed lines distinguish them from measured series.")
    title = spec["style"]["title"]
    legend = spec["style"]["legend"]
    layout.update({
        "font": {"size": font},
        "showlegend": legend != "none" and len(legend_shown) >= 2,
        "legend": {"right": {"x": 1.02, "y": 1, "xanchor": "left"}, "top": {"orientation": "h", "x": 0, "y": 1, "yanchor": "bottom"},
                   "bottom": {"orientation": "h", "x": 0, "y": -0.18, "yanchor": "top"}, "none": {}}[legend],
        "hovermode": "x unified" if spec["type"] == "line" else "closest",
        "margin": {"l": 72, "r": 120 if labels_at_ends else 24, "t": 56 if title else 28, "b": 56},
        "height": PANEL_HEIGHT * count + 84,
        "shapes": shapes,
        "annotations": annotations,
        **({"title": {"text": _esc(title), "x": 0, "xanchor": "left", "xref": "paper", "y": 1, "yref": "container",
                      "yanchor": "top", "pad": {"t": 8}, "font": {"size": font + 4}}} if title else {}),
    })
    return {"figure": {"data": traces, "layout": layout}, "notes": list(dict.fromkeys(notes)), "stats": table,
            "comparisons": comparisons, "fingerprints": prints, "metrics": metrics, "points": points}


def _alert_markers(data: dict, metric: str, axis: str, rng: dict | None, font: int,
                   shapes: list, annotations: list, notes: list, labelled: bool = True, x_scale: str = "linear") -> list[str]:
    """The run's first Critical and first Warning on this measure, as vertical lines. With one run
    in the panel each line is labelled; with several, labels would collide near the start, so the
    lines drawn are returned for the notes. One hidden by the range is said in the notes."""
    found = []
    for severity in ("critical", "warning"):
        steps = sorted(a["step"] for a in data.get("alerts", []) if a.get("severity") == severity
                       and a.get("step") is not None and a.get("metric") in (None, "", metric))
        if not steps:
            continue
        step = steps[0]
        name = severity.capitalize()
        if rng and not rng["from"] <= step <= rng["to"]:
            notes.append(f"This range hides the first {name} alert of {data['label']}, at step {step}.")
            continue
        if x_scale == "log" and step <= 0:
            notes.append(f"The first {name} alert of {data['label']}, at step {step}, cannot be shown on a log x axis.")
            continue
        shapes.append({"type": "line", "xref": f"x{axis}", "yref": f"y{axis} domain", "x0": step, "x1": step,
                       "y0": 0, "y1": 1, "line": {"color": STATUS[severity], "width": 1}, "meta": severity})
        found.append(f"{name} at step {step}")
        if labelled:
            annotations.append({"text": f"{name} · {step}", "x": _axis_pos(step, x_scale), "xref": f"x{axis}", "y": 1, "yref": f"y{axis} domain",
                                "xanchor": "left", "yanchor": "top", "xshift": 3, "showarrow": False, "meta": "marker-label",
                                "font": {"size": font - 2}})
    return found


def _first_metric(shown: list[str], datasets: dict[str, dict]) -> list[str]:
    for d in shown:
        if datasets[d]["series"]:
            return [next(iter(datasets[d]["series"]))]
    return []


def _x_title(spec: dict) -> str:
    label = spec["style"]["x_label"] or ("Step" if spec["x"] == "step" else spec["x"])
    unit = spec["units"].get(spec["x"])
    return f"{label} ({unit})" if unit else label


def _y_title(spec: dict, metric: str, measures: int) -> str:
    label = spec["style"]["y_label"] if spec["style"]["y_label"] and measures == 1 else metric
    if spec["layout"] == "difference":
        if spec["difference"] == "relative":
            return f"Relative difference in {label}"
        label = f"Δ {label}"
    unit = spec["units"].get(metric)
    return f"{label} ({unit})" if unit else label


def _axis_pos(value: float, scale: str) -> float:
    """Plotly places annotations on a log axis by the value's log10."""
    return math.log10(value) if scale == "log" and value > 0 else value


def _short(label: str) -> str:
    return label if len(label) <= 24 else label[:23] + "…"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _histogram(spec: dict, datasets: dict[str, dict]) -> dict:
    """Deterministic counts with common bin edges for every run in a measure."""
    shown = [d for d in spec["datasets"] if d in datasets]
    metrics = spec["metrics"] or _first_metric(shown, datasets)
    raw, prints, rows, notes = {}, {}, [], []
    for dataset in shown:
        for metric in metrics:
            points = xy(datasets[dataset]["series"], metric, spec["x"])
            if points is None:
                notes.append(f"{datasets[dataset]['label']} has no {metric} against {spec['x']}.")
                continue
            prints[f"{dataset}|{metric}"] = fingerprint(points)
            points = in_range(points, spec["range"])
            raw[f"{dataset}|{metric}"] = points
            rows.append({"dataset": dataset, "label": datasets[dataset]["label"], "metric": metric, **stats(points)})
            gaps = sum(value is None for _, value in points)
            if gaps:
                notes.append(f"{datasets[dataset]['label']}: {gaps} missing {metric} values are excluded from histogram counts.")
    synthetic = {d: {**datasets[d], "series": {}, "alerts": [], "thinned": []} for d in shown}
    bounds = {}
    for metric in metrics:
        values = [value for d in shown for _, value in raw.get(f"{d}|{metric}", [])
                  if value is not None and (spec["x_scale"] != "log" or value > 0)]
        edges = histogram_edges(values, spec["histogram"]["bins"])
        bounds[metric] = edges
        for dataset in shown:
            points = raw.get(f"{dataset}|{metric}")
            if points is None:
                continue
            if spec["x_scale"] == "log":
                hidden = sum(value is not None and value <= 0 for _, value in points)
                if hidden:
                    notes.append(f"{datasets[dataset]['label']}: {hidden} {metric} values at or below zero cannot be binned on a log x axis.")
                points = [(x, value) for x, value in points if value is None or value > 0]
            synthetic[dataset]["series"][metric] = histogram(points, edges)
    # The standard renderer provides consistent panels, legends and theme slots.
    template = {**spec, "type": "line", "layout": "separate" if spec["layout"] == "separate" else "overlay",
                "baseline": None, "x": "step", "range": None}
    result = build(template, synthetic)
    for trace in result["figure"]["data"]:
        trace["type"], trace["opacity"] = "bar", .65
        trace["marker"] = {"color": trace.pop("line")["color"]}
        trace.pop("mode", None)
        trace["meta"]["derived"] = "histogram"
        metric = trace["meta"]["metric"]
        edges = bounds[metric]
        widths = [right - left for left, right in zip(edges, edges[1:])]
        if any(not math.isfinite(width) or width <= 0 for width in widths):
            raise SpecError("These histogram bin widths exceed the numeric drawing range. Increase the bin count.")
        trace["width"] = widths
        trace["customdata"] = [[left, right] for left, right in zip(edges, edges[1:])]
        trace["hovertemplate"] = "[%{customdata[0]:.6g}, %{customdata[1]:.6g}]: %{y} observations<extra>%{fullData.name}</extra>"
    layout = result["figure"]["layout"]
    panels = [(d, m) for d in shown for m in metrics] if template["layout"] == "separate" else [(None, m) for m in metrics]
    for i, (_, metric) in enumerate(panels):
        suffix = "" if i == 0 else str(i + 1)
        unit = spec["units"].get(metric)
        layout[f"xaxis{suffix}"]["title"] = {"text": _esc(f"{metric} ({unit})" if unit else metric)}
        layout[f"xaxis{suffix}"].pop("matches", None)  # measures can have different ranges and units
        layout[f"xaxis{suffix}"]["showticklabels"] = True
        layout[f"yaxis{suffix}"]["title"] = {"text": "Count"}
    layout["barmode"] = "overlay"
    layout["annotations"] = [a for a in layout["annotations"] if a.get("meta") in ("panel-title", "note")]
    layout["margin"]["r"] = 24
    notes.append(f"Histogram counts use {spec['histogram']['bins']} shared bins per measure; gaps are never counted as zero.")
    if spec["y_scale"] == "log":
        notes.append("Empty bins have zero counts and appear as gaps on the log count axis.")
    if any(datasets[d].get("alerts") for d in shown):
        notes.append("Histograms show distributions, so alert steps are listed in the run data rather than placed on the value axis.")
    result.update(notes=list(dict.fromkeys(notes)), stats=rows, comparisons=[], fingerprints=prints, points=raw)
    return result


def _sweep(spec: dict, datasets: dict[str, dict]) -> dict:
    """One actual/interpolated output value per experiment against a numeric parameter."""
    parameter, wanted = spec["sweep"]["parameter"], spec["sweep"]["step"]
    shown = [d for d in spec["datasets"] if d in datasets]
    metrics = spec["metrics"] or _first_metric(shown, datasets)
    synthetic, samples, prints, notes = {}, {}, {}, []
    for dataset in shown:
        data = datasets[dataset]
        if data.get("source") != "experiment":
            notes.append(f"{data['label']} is not an experiment with recorded parameters, so it is excluded from the sweep.")
            continue
        value = data.get("parameters", {}).get(parameter)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            notes.append(f"{data['label']} has no numeric {parameter or 'selected parameter'}, so it is excluded from the sweep.")
            continue
        synthetic[dataset] = {**data, "series": {}, "alerts": [], "thinned": []}
        for metric in metrics:
            points = data["series"].get(metric)
            if not points:
                continue
            prints[f"{dataset}|{metric}"] = fingerprint(points)
            if wanted is None:
                at, measured = points[-1]
                estimated = False
            else:
                at = wanted
                selected = align([(wanted, 0)], points)
                measured = selected[0][1] if selected else None
                estimated = measured is not None and wanted not in {x for x, _ in points}
            synthetic[dataset]["series"][metric] = [(value, measured)]
            samples[f"{dataset}|{metric}"] = {"parameter": parameter, "parameter_value": value,
                                              "at_step": at, "interpolated": estimated, "derived": "sweep"}
            if measured is None:
                notes.append(f"{data['label']}: {metric} is missing at step {at}; the sweep retains a gap, without extrapolation.")
            elif estimated:
                notes.append(f"{data['label']}: {metric} at step {at} is a straight-line estimate between measured steps.")
    template = {**spec, "datasets": [d for d in shown if d in synthetic], "type": "scatter", "layout": "overlay",
                "baseline": None, "x": "step"}
    # Scatter's three-run palette limit is a control rule; a sweep has one
    # colored marker for each experiment, with the common parameter ordering.
    result = build(template, synthetic)
    layout = result["figure"]["layout"]
    label = parameter or "Experiment parameter"
    unit = spec["units"].get(parameter)
    for i, metric in enumerate(metrics):
        suffix = "" if i == 0 else str(i + 1)
        layout[f"xaxis{suffix}"]["title"] = {"text": _esc(f"{label} ({unit})" if unit else label)}
        points = sorted((data["series"][metric][0] for data in synthetic.values() if metric in data["series"]), key=lambda p: p[0])
        points = in_range(points, spec["range"])
        if spec["x_scale"] == "log":
            points = [p for p in points if p[0] > 0]
        if spec["y_scale"] == "log":
            points, _ = for_log(points)
        if len(points) >= 2 and len({x for x, _ in points}) == len(points):
            result["figure"]["data"].insert(0, {
                "type": "scatter", "mode": "lines", "x": [x for x, _ in points], "y": [y for _, y in points],
                "xaxis": f"x{suffix}", "yaxis": f"y{suffix}", "connectgaps": False, "showlegend": False,
                "line": {"color": "#788493", "width": spec["style"]["line_width"]}, "hoverinfo": "skip",
                "meta": {"derived": "sweep", "aggregate": True, "metric": metric}})
        elif len(points) >= 2:
            notes.append(f"Multiple experiments have the same {parameter}; markers are shown without a connecting sweep line.")
    for trace in result["figure"]["data"]:
        meta = trace.get("meta", {})
        key = f"{meta.get('dataset')}|{meta.get('metric')}"
        if key in samples:
            meta.update(samples[key])
            trace["marker"]["symbol"] = "diamond" if samples[key]["interpolated"] else "circle"
    for row in result["stats"]:
        row.update(samples.get(f"{row['dataset']}|{row['metric']}", {}))
    notes += result["notes"]
    if wanted is None:
        notes.append("Final values use each experiment's last recorded step, including a gap if that final value is missing.")
    else:
        notes.append(f"Sweep values at step {wanted} use only measured values or interpolation inside the recorded range, never across a gap.")
    result.update(notes=list(dict.fromkeys(notes)), comparisons=[], fingerprints=prints)
    return result
