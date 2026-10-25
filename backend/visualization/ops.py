"""The operations that change a graph: the only way the page or an agent changes a spec.

Each operation is data ({"op": name, ...arguments}), checked against the spec and the viewer's
catalog before it applies; one that fails leaves the graph as it was and says why. There is no
code, expression or file path anywhere in an operation. Units are the researcher's alone.
"""
from __future__ import annotations

import copy
from typing import Any

from backend.visualization.spec import (
    GRAPH_TYPES, LAYOUTS, LEGEND_PLACES, MAX_DATASETS, MAX_METRICS, MAX_SCATTER_DATASETS, MAX_TEXT, PRESETS, SCALES,
    SpecError, assign_colors, new_spec, normalized,
)

# operation -> the arguments it accepts
ARGUMENTS = {
    "create_graph": {"datasets", "metrics", "x", "type"},
    "select_runs": {"datasets"},
    "add_run": {"dataset"},
    "remove_run": {"dataset"},
    "change_metric": {"metrics"},
    "change_axis": {"x", "x_scale", "y_scale"},
    "set_time_range": {"from", "to", "clear"},
    "compare_runs": {"datasets", "mode", "difference", "threshold"},
    "change_graph_type": {"type"},
    "add_annotation": {"x", "y", "text"},
    "remove_annotation": {"index"},
    "update_style": {"title", "x_label", "y_label", "legend", "preset", "line_width"},
    "set_units": {"measure", "unit"},
    "set_baseline": {"dataset"},
    "configure_histogram": {"bins"},
    "configure_sweep": {"parameter", "step"},
    "set_resolution": {"mode"},
    "save_visualization": {"title"},
    "export_figure": {"format"},
}
RESEARCHER_ONLY = {"set_units", "set_resolution"}
LATER = {}


class OpRefused(Exception):
    pass


def apply(spec: dict, ops: Any, catalog: list[dict], actor: str = "researcher") -> dict:
    """Apply operations in order. Returns {"spec", "applied", "refused": [{"op", "reason"}],
    "actions": [{"save": title} | {"export": format}]}. A refused operation changes nothing."""
    known = {d["id"]: d for d in catalog}
    current = normalized(spec)
    applied, refused, actions = [], [], []
    for op in ops if isinstance(ops, list) else [ops]:
        try:
            current, action = _apply_one(current, op, known, actor)
        except (OpRefused, SpecError) as exc:
            refused.append({"op": op, "reason": str(exc)})
            continue
        applied.append(op)
        if action:
            actions.append(action)
    return {"spec": current, "applied": applied, "refused": refused, "actions": actions}


def _apply_one(spec: dict, op: Any, known: dict, actor: str) -> tuple[dict, dict | None]:
    if not isinstance(op, dict) or not isinstance(op.get("op"), str):
        raise OpRefused('Each operation is an object with "op".')
    name = op["op"]
    if name not in ARGUMENTS:
        raise OpRefused(f'There is no operation "{name}".')
    if name in RESEARCHER_ONLY and actor != "researcher":
        raise OpRefused("Only the researcher sets units, so they're never guessed." if name == "set_units"
                        else "Only the researcher requests full-resolution source data.")
    extra = set(op) - ARGUMENTS[name] - {"op"}
    if extra:
        raise OpRefused(f"{name} doesn't take {', '.join(sorted(extra))}.")
    if name in LATER:
        raise OpRefused(LATER[name])
    s = copy.deepcopy(spec)
    action = None

    if name == "create_graph":
        fresh = new_spec(s["project_id"])
        fresh["units"], fresh["style"], fresh["resolution"] = s["units"], s["style"], s["resolution"]
        fresh["datasets"] = _datasets(op.get("datasets"), known)
        s = fresh
        if "metrics" in op:
            s["metrics"] = _metrics(op["metrics"], s["datasets"], known)
        if "x" in op:
            s["x"] = _x(op["x"], s["datasets"], known)
        if "type" in op:
            s["type"] = _choice(op["type"], GRAPH_TYPES, "type")
    elif name == "select_runs":
        s["datasets"] = _datasets(op.get("datasets"), known)
        if s["baseline"] not in s["datasets"]:
            s["baseline"] = None
            if s["layout"] == "difference":
                s["layout"] = "overlay"
    elif name == "add_run":
        dataset = _dataset(op.get("dataset"), known)
        if dataset in s["datasets"]:
            raise OpRefused(f"{known[dataset]['label']} is already on the graph.")
        s["datasets"].append(dataset)
    elif name == "remove_run":
        dataset = op.get("dataset")
        if dataset not in s["datasets"]:
            raise OpRefused(f"{dataset} isn't on the graph.")
        if dataset == s["baseline"] and s["layout"] == "difference":
            raise OpRefused("Choose another baseline before removing the current baseline from a difference plot.")
        s["datasets"].remove(dataset)
        if dataset == s["baseline"]:
            s["baseline"] = None
    elif name == "change_metric":
        s["metrics"] = _metrics(op.get("metrics"), s["datasets"], known)
    elif name == "change_axis":
        if not {"x", "x_scale", "y_scale"} & set(op):
            raise OpRefused("change_axis needs x, x_scale or y_scale.")
        if "x" in op:
            s["x"] = _x(op["x"], s["datasets"], known)
        for key in ("x_scale", "y_scale"):
            if key in op:
                s[key] = _choice(op[key], SCALES, key)
    elif name == "set_time_range":
        if op.get("clear"):
            s["range"] = None
        else:
            low, high = _number(op.get("from"), "from"), _number(op.get("to"), "to")
            if not low < high:
                raise OpRefused("A range needs from < to.")
            s["range"] = {"from": low, "to": high}
    elif name == "compare_runs":
        mode = _choice(op.get("mode", "overlay"), LAYOUTS, "mode")
        if "datasets" in op:
            s["datasets"] = _datasets(op["datasets"], known)
        if s["baseline"] not in s["datasets"]:
            s["baseline"] = None
        if mode == "difference":
            if len(s["datasets"]) < 2:
                raise OpRefused("A difference plot needs a baseline and at least one other run.")
            if s["type"] not in ("line", "scatter"):
                raise OpRefused("Difference plots use lines or scatter points.")
            if s["baseline"] not in s["datasets"]:
                s["baseline"] = s["datasets"][0]
            if "difference" in op:
                s["difference"] = _choice(op["difference"], ("absolute", "relative"), "difference")
            if "threshold" in op:
                threshold = _number(op["threshold"], "threshold")
                if threshold < 0:
                    raise OpRefused("The relative threshold must be nonnegative.")
                s["relative_threshold"] = threshold
        s["layout"] = mode
    elif name == "change_graph_type":
        s["type"] = _choice(op.get("type"), GRAPH_TYPES, "type")
        if s["type"] in ("histogram", "sweep"):
            s["layout"] = "overlay"
    elif name == "set_baseline":
        dataset = _dataset(op.get("dataset"), known)
        if dataset not in s["datasets"]:
            raise OpRefused("Select that run on the graph before choosing it as the baseline.")
        s["baseline"] = dataset
    elif name == "configure_histogram":
        bins = op.get("bins", s["histogram"]["bins"])
        if not isinstance(bins, int) or isinstance(bins, bool) or not 1 <= bins <= 200:
            raise OpRefused("Histogram bins must be an integer from 1 to 200.")
        s.update(type="histogram", layout="overlay", histogram={"bins": bins})
    elif name == "configure_sweep":
        parameter = _text(op.get("parameter", s["sweep"]["parameter"]), "label")
        if not parameter:
            raise OpRefused("Choose a numeric experiment parameter for the sweep.")
        step = op.get("step", s["sweep"]["step"])
        if step is not None:
            step = _number(step, "step")
        s.update(type="sweep", layout="overlay", sweep={"parameter": parameter, "step": step})
    elif name == "set_resolution":
        s["resolution"] = _choice(op.get("mode"), ("recorded", "full"), "mode")
    elif name == "add_annotation":
        text = _text(op.get("text"), "annotation")
        if not text:
            raise OpRefused("An annotation needs text.")
        s["annotations"].append({"x": _number(op.get("x"), "x"),
                                 "y": None if op.get("y") is None else _number(op["y"], "y"), "text": text})
    elif name == "remove_annotation":
        index = op.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(s["annotations"]):
            raise OpRefused("No annotation with that number.")
        s["annotations"].pop(index)
    elif name == "update_style":
        changes = {k: v for k, v in op.items() if k != "op"}
        if not changes:
            raise OpRefused("update_style needs at least one of: " + ", ".join(sorted(ARGUMENTS["update_style"])) + ".")
        for key, value in changes.items():
            if key in ("title", "x_label", "y_label"):
                s["style"][key] = _text(value, "title" if key == "title" else "label")
            elif key == "legend":
                s["style"]["legend"] = _choice(value, LEGEND_PLACES, "legend")
            elif key == "preset":
                s["style"]["preset"] = _choice(value, PRESETS, "preset")
            elif key == "line_width":
                width = _number(value, "line_width")
                if not 1 <= width <= 4:
                    raise OpRefused("line_width is 1 to 4.")
                s["style"]["line_width"] = width
    elif name == "set_units":
        measure = op.get("measure")
        if not isinstance(measure, str) or not measure:
            raise OpRefused("set_units needs the measure (or step).")
        unit = _text(op.get("unit", ""), "unit")
        if unit:
            s["units"][measure] = unit
        else:
            s["units"].pop(measure, None)
    elif name == "save_visualization":
        title = _text(op.get("title", ""), "title")
        if title:
            s["style"]["title"] = title
        action = {"save": title}
    elif name == "export_figure":
        fmt = _choice(op.get("format", "png"), ("png", "svg", "pdf"), "format")
        action = {"export": fmt}

    if s["type"] == "scatter" and len(s["datasets"]) > MAX_SCATTER_DATASETS:
        raise OpRefused(f"A scatter shows at most {MAX_SCATTER_DATASETS} runs, so their colours stay distinguishable. "
                        "Remove some runs, or use a line graph.")
    assign_colors(s)
    return normalized(s), action


def _dataset(value: Any, known: dict) -> str:
    if not isinstance(value, str) or value not in known:
        raise OpRefused(f'"{value}" isn\'t one of the runs you can graph in this project.')
    return value


def _datasets(value: Any, known: dict) -> list[str]:
    if not isinstance(value, list) or not value:
        raise OpRefused("datasets must be a list of runs.")
    picked = list(dict.fromkeys(_dataset(v, known) for v in value))
    if len(picked) > MAX_DATASETS:
        raise OpRefused(f"At most {MAX_DATASETS} runs on one graph, so each keeps a distinguishable colour.")
    return picked


def _metrics(value: Any, datasets: list[str], known: dict) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or not all(isinstance(m, str) for m in value):
        raise OpRefused("metrics must be one or more measure names.")
    picked = list(dict.fromkeys(value))
    if len(picked) > MAX_METRICS:
        raise OpRefused(f"At most {MAX_METRICS} measures on one graph (each gets its own panel).")
    available = {m for d in datasets for m in known[d].get("metrics", [])}
    unknown = [m for m in picked if m not in available]
    if unknown:
        raise OpRefused(f"No run on the graph has {', '.join(unknown)}. Measures here: {', '.join(sorted(available)) or 'none'}.")
    return picked


def _x(value: Any, datasets: list[str], known: dict) -> str:
    if value == "step":
        return value
    available = {m for d in datasets for m in known[d].get("metrics", [])}
    if not isinstance(value, str) or value not in available:
        raise OpRefused(f'x must be "step" or a measure the runs log ({", ".join(sorted(available)) or "none"}).')
    return value


def _choice(value: Any, options: tuple, name: str) -> str:
    if value not in options:
        raise OpRefused(f"{name} must be one of: {', '.join(options)}.")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
        raise OpRefused(f"{name} must be a number.")
    return value


def _text(value: Any, kind: str) -> str:
    if not isinstance(value, str):
        raise OpRefused("Labels are plain text.")
    text = " ".join(value.split())
    if len(text) > MAX_TEXT[kind]:
        raise OpRefused(f"That text is longer than {MAX_TEXT[kind]} characters.")
    return text
