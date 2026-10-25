"""The graph spec: one JSON document that fully describes a graph, saved and reopened as is.

The data is never in the spec, only references to it (catalog IDs such as "run:<id>" or
"exp:<id>"), so a graph can always be rebuilt from the source and never changes it.
"""
from __future__ import annotations

import copy
from typing import Any

SPEC_VERSION = 1
GRAPH_TYPES = ("line", "scatter", "histogram", "sweep")
LAYOUTS = ("overlay", "separate", "difference")
SCALES = ("linear", "log")
PRESETS = ("screen", "paper", "poster")
LEGEND_PLACES = ("right", "top", "bottom", "none")
PALETTE_SLOTS = 8  # the validated categorical palette (figure.py); a 9th run is refused, never a new colour
MAX_DATASETS = PALETTE_SLOTS
MAX_SCATTER_DATASETS = 3  # the palette's first three slots stay distinguishable on a scatter
MAX_METRICS = 3
MAX_PANELS = 8
MAX_ANNOTATIONS = 20
MAX_TEXT = {"title": 120, "label": 80, "annotation": 120, "unit": 24}


class SpecError(ValueError):
    """A spec that can't be drawn as it stands; the message says why."""


def new_spec(project_id: str) -> dict:
    return {
        "version": SPEC_VERSION,
        "project_id": project_id,
        "datasets": [],          # catalog IDs, in the order they were added
        "colors": {},            # catalog ID -> palette slot (0-7): a run keeps its colour
        "metrics": [],           # measure names, one panel each
        "x": "step",             # "step", or a measure logged by every dataset shown
        "type": "line",
        "layout": "overlay",     # overlay: every run in each measure's panel; separate: a panel per run and measure
        "baseline": None,        # selected dataset used for target - baseline differences
        "difference": "absolute",
        "relative_threshold": 1e-12,
        "histogram": {"bins": 30},
        "sweep": {"parameter": "", "step": None},
        "resolution": "recorded",
        "x_scale": "linear",
        "y_scale": "linear",
        "range": None,           # {"from": number, "to": number} in x units, inclusive
        "annotations": [],       # {"x": number, "y": number or None, "text": str}
        "units": {},             # measure (or "step") -> unit, set by the researcher only
        "style": {"title": "", "x_label": "", "y_label": "", "legend": "top", "preset": "screen", "line_width": 2},
    }


def normalized(spec: Any) -> dict:
    """A copy of a spec with every field present and of the right kind, or SpecError."""
    if not isinstance(spec, dict) or not isinstance(spec.get("project_id"), str):
        raise SpecError("A graph needs its project.")
    base = new_spec(spec["project_id"])
    out = copy.deepcopy(base)
    for key in base:
        if key in spec and key not in ("version", "project_id"):
            out[key] = copy.deepcopy(spec[key])
    style = spec.get("style")
    if style is not None and not isinstance(style, dict):
        raise SpecError("style must be an object.")
    if style and set(style) - set(base["style"]):
        raise SpecError("Unknown style settings.")
    out["style"] = {**base["style"], **(style or {})}
    _check(out)
    assign_colors(out)
    return out


def _check(spec: dict) -> None:
    def need(condition: bool, message: str) -> None:
        if not condition:
            raise SpecError(message)

    need(isinstance(spec["datasets"], list) and all(isinstance(d, str) for d in spec["datasets"]), "datasets must be catalog IDs.")
    need(len(set(spec["datasets"])) == len(spec["datasets"]), "A run is listed twice.")
    need(len(spec["datasets"]) <= MAX_DATASETS, f"At most {MAX_DATASETS} runs on one graph.")
    need(isinstance(spec["colors"], dict) and all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                                                and 0 <= v < PALETTE_SLOTS for k, v in spec["colors"].items()),
         "Colours must be palette slots.")
    slots = [slot for dataset, slot in spec["colors"].items() if dataset in spec["datasets"]]
    need(len(set(slots)) == len(slots), "Each run needs a distinct colour slot.")
    need(isinstance(spec["metrics"], list) and all(isinstance(m, str) for m in spec["metrics"]), "metrics must be measure names.")
    need(len(spec["metrics"]) <= MAX_METRICS, f"At most {MAX_METRICS} measures on one graph.")
    need(len(set(spec["metrics"])) == len(spec["metrics"]), "A measure is listed twice.")
    need(isinstance(spec["x"], str) and spec["x"], "x must be step or a measure.")
    need(spec["type"] in GRAPH_TYPES, f"type must be one of {', '.join(GRAPH_TYPES)}.")
    need(spec["layout"] in LAYOUTS, f"layout must be one of {', '.join(LAYOUTS)}.")
    need(spec["baseline"] is None or spec["baseline"] in spec["datasets"], "The baseline must be a run selected on the graph.")
    need(spec["difference"] in ("absolute", "relative"), "Differences are absolute or relative.")
    need(_number(spec["relative_threshold"]) and spec["relative_threshold"] >= 0, "The relative threshold must be a nonnegative number.")
    need(spec["layout"] != "difference" or spec["baseline"] is not None, "Choose a baseline for difference plots.")
    need(spec["layout"] != "difference" or spec["type"] in ("line", "scatter"), "Difference plots use lines or scatter points.")
    need(spec["resolution"] in ("recorded", "full"), "Resolution is recorded or full.")
    histogram = spec["histogram"]
    need(isinstance(histogram, dict) and isinstance(histogram.get("bins"), int) and not isinstance(histogram["bins"], bool)
         and 1 <= histogram["bins"] <= 200, "Histogram bins must be an integer from 1 to 200.")
    sweep = spec["sweep"]
    need(isinstance(sweep, dict) and isinstance(sweep.get("parameter"), str) and len(sweep["parameter"]) <= MAX_TEXT["label"]
         and (sweep.get("step") is None or _number(sweep["step"])), "A sweep needs a parameter and a numeric step or final.")
    need(spec["x_scale"] in SCALES and spec["y_scale"] in SCALES, "Scales are linear or log.")
    rng = spec["range"]
    need(rng is None or (isinstance(rng, dict) and _number(rng.get("from")) and _number(rng.get("to"))
                         and rng["from"] < rng["to"]), "A range needs from < to.")
    need(isinstance(spec["annotations"], list) and len(spec["annotations"]) <= MAX_ANNOTATIONS, "Too many annotations.")
    for a in spec["annotations"]:
        need(isinstance(a, dict) and _number(a.get("x")) and (a.get("y") is None or _number(a.get("y")))
             and isinstance(a.get("text"), str) and 0 < len(a["text"]) <= MAX_TEXT["annotation"], "An annotation needs x and short text.")
    need(isinstance(spec["units"], dict) and all(isinstance(v, str) and len(v) <= MAX_TEXT["unit"] for v in spec["units"].values()),
         "Units are short text.")
    style = spec["style"]
    need(style["legend"] in LEGEND_PLACES and style["preset"] in PRESETS, "Unknown legend place or preset.")
    need(_number(style["line_width"]) and 1 <= style["line_width"] <= 4, "Line width is 1 to 4.")
    for key, limit in (("title", MAX_TEXT["title"]), ("x_label", MAX_TEXT["label"]), ("y_label", MAX_TEXT["label"])):
        need(isinstance(style[key], str) and len(style[key]) <= limit, f"{key} is at most {limit} characters.")
    if spec["type"] == "scatter":
        need(len(spec["datasets"]) <= MAX_SCATTER_DATASETS, f"A scatter shows at most {MAX_SCATTER_DATASETS} runs, so their colours stay distinguishable.")


def assign_colors(spec: dict) -> None:
    """Give each dataset without one the lowest free palette slot; drop slots of removed datasets.
    Colours follow the run, never its position, so removing one never repaints the others."""
    colors = {d: s for d, s in spec["colors"].items() if d in spec["datasets"]}
    for dataset in spec["datasets"]:
        if dataset not in colors:
            colors[dataset] = min(set(range(PALETTE_SLOTS)) - set(colors.values()))
    spec["colors"] = colors


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")
