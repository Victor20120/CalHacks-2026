"""The pictures in an onboarding lesson. The Super Brain describes each one; ExpandRG checks the
description and the dashboard draws it, so the agent never sends SVG, HTML or code.

Three kinds:
- "flow": the steps the code goes through, in order, each with the lines that do it, and an
  optional loop around some of them ("for each time step").
- "grid": a small grid of points or cells (up to 9 by 9) with labels, tones and arrows: a
  stencil, the points an update reads, a boundary.
- "plot": curves of formulas in x over a range, e.g. an amplification factor against r. The
  formulas are evaluated here, by a small evaluator that knows only arithmetic, a few math
  functions, pi, e and x, so the curve is computed, never invented. Non-finite values are gaps.

check() returns the pictures that pass, and a note for each one left out.
"""
from __future__ import annotations

import ast
import math
from typing import Any, Callable

MAX_PICTURES = 4
MAX_STEPS = 10
MAX_GRID = 9
MAX_CELLS = MAX_GRID * MAX_GRID
MAX_ARROWS = 16
MAX_CURVES = 3
MAX_MARKS = 4
SAMPLES = 121
MAX_NODES = 80  # in one formula
MAX_RANGE = 1e6
LIMITS = {"title": 100, "caption": 400, "label": 60, "detail": 200, "loop": 40, "cell": 10, "arrow": 12, "axis": 40,
          "curve": 40, "expr": 160, "mark": 30}
TONES = ("focus", "uses", "boundary", "empty")

FUNCTIONS: dict[str, Callable[..., float]] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "exp": math.exp, "log": math.log, "log10": math.log10,
    "sqrt": math.sqrt, "abs": abs, "min": min, "max": max, "floor": math.floor, "ceil": math.ceil,
}
CONSTANTS = {"pi": math.pi, "e": math.e}
OPERATORS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
             ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}


class FormulaRefused(Exception):
    """Not a ValueError: the evaluator turns those (log of a negative, say) into gaps."""


def check(value: Any, lines: Callable[[Any], dict]) -> tuple[list[dict], list[str]]:
    """The pictures that pass, and notes on the ones left out. lines(value) checks a line range
    against the code that was sent (lessons.Code.lines)."""
    pictures, notes = [], []
    for raw in (value[:MAX_PICTURES] if isinstance(value, list) else []):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("type")
        build = {"flow": _flow, "grid": _grid, "plot": _plot}.get(kind)
        if build is None:
            notes.append(f"A picture of an unknown kind ({_text(kind, 'label') or 'none'}) was left out.")
            continue
        try:
            picture = build(raw, lines)
        except FormulaRefused as exc:
            notes.append(f"The plot “{_text(raw.get('title'), 'title') or 'untitled'}” was left out: {exc}")
            continue
        if picture is None:
            notes.append(f"The {kind} picture “{_text(raw.get('title'), 'title') or 'untitled'}” was left out: it was incomplete.")
            continue
        pictures.append({"type": kind, "title": _text(raw.get("title"), "title"), "caption": _text(raw.get("caption"), "caption"),
                         **picture})
    return pictures, notes


def _flow(raw: dict, lines: Callable) -> dict | None:
    steps = [{"label": _text(s.get("label"), "label"), "detail": _text(s.get("detail"), "detail"), **lines(s.get("lines"))}
             for s in (raw.get("steps") or [])[:MAX_STEPS] if isinstance(s, dict) and _text(s.get("label"), "label")]
    if len(steps) < 2:
        return None
    loop = raw.get("loop") if isinstance(raw.get("loop"), dict) else None
    if loop:
        first, last = loop.get("from"), loop.get("to")
        ok = all(isinstance(i, int) and not isinstance(i, bool) for i in (first, last)) and 0 <= first <= last < len(steps)
        loop = {"from": first, "to": last, "label": _text(loop.get("label"), "loop")} if ok else None
    return {"steps": steps, "loop": loop}


def _grid(raw: dict, lines: Callable) -> dict | None:
    rows, cols = raw.get("rows"), raw.get("cols")
    if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_GRID for n in (rows, cols)):
        return None
    inside = lambda r, c: isinstance(r, int) and isinstance(c, int) and 0 <= r < rows and 0 <= c < cols  # noqa: E731
    cells, seen = [], set()
    for cell in (raw.get("cells") or [])[:MAX_CELLS]:
        if isinstance(cell, dict) and inside(cell.get("row"), cell.get("col")) and (cell["row"], cell["col"]) not in seen:
            seen.add((cell["row"], cell["col"]))
            cells.append({"row": cell["row"], "col": cell["col"], "label": _text(cell.get("label"), "cell"),
                          "tone": cell.get("tone") if cell.get("tone") in TONES else "empty"})
    arrows = []
    for arrow in (raw.get("arrows") or [])[:MAX_ARROWS]:
        ends = [arrow.get("from"), arrow.get("to")] if isinstance(arrow, dict) else []
        if len(ends) == 2 and all(isinstance(e, list) and len(e) == 2 and inside(*e) for e in ends) and ends[0] != ends[1]:
            arrows.append({"from": ends[0], "to": ends[1], "label": _text(arrow.get("label"), "arrow")})
    if not cells:
        return None
    return {"rows": rows, "cols": cols, "cells": cells, "arrows": arrows, "row_label": _text(raw.get("row_label"), "axis"),
            "col_label": _text(raw.get("col_label"), "axis"), **lines(raw.get("lines"))}


def _plot(raw: dict, lines: Callable) -> dict | None:
    x = raw.get("x") if isinstance(raw.get("x"), dict) else {}
    y = raw.get("y") if isinstance(raw.get("y"), dict) else {}
    low, high = x.get("from"), x.get("to")
    if not all(_number(v) for v in (low, high)) or not low < high or max(abs(low), abs(high)) > MAX_RANGE:
        raise FormulaRefused("its x range needs from < to, both numbers within ±1,000,000.")
    log_x = x.get("log") is True and low > 0
    xs = [math.exp(math.log(low) + (math.log(high) - math.log(low)) * i / (SAMPLES - 1)) if log_x
          else low + (high - low) * i / (SAMPLES - 1) for i in range(SAMPLES)]
    curves = []
    for curve in (raw.get("curves") or [])[:MAX_CURVES]:
        if not isinstance(curve, dict):
            continue
        expr = _text(curve.get("expr"), "expr")
        formula = compile_formula(expr)
        curves.append({"label": _text(curve.get("label"), "curve") or expr, "expr": expr,
                       "points": [[round(v, 12), formula(v)] for v in xs]})
    if not curves:
        return None
    if not any(p[1] is not None for c in curves for p in c["points"]):
        raise FormulaRefused("its formulas have no finite value in that range.")
    marks = [{"x": m["x"], "label": _text(m.get("label"), "mark")} for m in (raw.get("marks") or [])[:MAX_MARKS]
             if isinstance(m, dict) and _number(m.get("x")) and low <= m["x"] <= high]
    return {"x": {"label": _text(x.get("label"), "axis") or "x", "from": low, "to": high, "log": log_x},
            "y": {"label": _text(y.get("label"), "axis"), "log": y.get("log") is True},
            "curves": curves, "marks": marks, "computed": True, **lines(raw.get("lines"))}


def compile_formula(expr: str) -> Callable[[float], float | None]:
    """A formula in x as a function returning a finite float, or None where it isn't defined. Only
    numbers, x, pi, e, + - * / ** %, and the FUNCTIONS are allowed; "^" means a power."""
    if not expr:
        raise FormulaRefused("it had no formula.")
    try:
        tree = ast.parse(expr.replace("^", "**"), mode="eval")
    except SyntaxError:
        raise FormulaRefused(f"“{expr}” isn't a formula ExpandRG can read.") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise FormulaRefused("its formula is too long.")
    for node in nodes:
        if isinstance(node, (ast.Expression, ast.Load)) or type(node) in OPERATORS or isinstance(node, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.Constant) and _number(node.value):
            continue
        if isinstance(node, ast.Name) and (node.id == "x" or node.id in CONSTANTS or node.id in FUNCTIONS):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            continue
        raise FormulaRefused(f"“{expr}” uses something other than x, numbers, pi, e and "
                             f"{', '.join(sorted(FUNCTIONS))}.")

    def value(node: ast.AST, x: float) -> float:
        if isinstance(node, ast.Expression):
            return value(node.body, x)
        if isinstance(node, ast.Constant):
            return float(node.value)  # floats overflow at once, where integer powers could run for ever
        if isinstance(node, ast.Name):
            if node.id in FUNCTIONS:
                raise FormulaRefused(f"{node.id} is a function: write {node.id}(...).")
            return x if node.id == "x" else CONSTANTS[node.id]
        if isinstance(node, ast.UnaryOp):
            operand = value(node.operand, x)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.BinOp):
            return OPERATORS[type(node.op)](value(node.left, x), value(node.right, x))
        if isinstance(node, ast.Call):
            return float(FUNCTIONS[node.func.id](*[value(a, x) for a in node.args]))
        raise FormulaRefused("unexpected formula")

    def formula(x: float) -> float | None:
        try:
            result = value(tree, x)
        except (ArithmeticError, ValueError, TypeError):  # 1/0, log(-1), overflow, a wrong number of arguments
            return None
        if isinstance(result, complex) or not math.isfinite(result):
            return None
        return round(result, 12)

    formula(1.0)  # refuses a misused function name now, not at every point
    return formula


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text if len(text) <= LIMITS[field] else text[: LIMITS[field] - 1].rstrip() + "…"
