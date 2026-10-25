"""The numerics behind every graph. Pure functions on (x, y) points, where y is None for a gap
(NaN, Inf or a missing value). Nothing here invents a value: gaps stay gaps, alignment stays
inside the overlap of two series and never interpolates across a gap, and display downsampling
keeps each bucket's lowest and highest point so a spike or blow-up is never smoothed away.
"""
from __future__ import annotations

import bisect
import math

Point = tuple  # (x, y): x a number, y a number or None

MAX_DISPLAY_POINTS = 2_000  # per trace, after which downsample() keeps each bucket's extremes


def xy(series: dict, metric: str, x: str) -> list[Point] | None:
    """A measure's points against x ("step", or another measure logged at the same steps).
    None when the dataset doesn't have the measure. A step where x is a gap breaks the line."""
    points = series.get(metric)
    if points is None:
        return None
    if x == "step":
        return [(step, value) for step, value in points]
    xs = series.get(x)
    if xs is None:
        return None
    at = dict(xs)
    out: list[Point] = []
    for step, value in points:
        x_value = at.get(step)
        if x_value is None:
            if out and out[-1][1] is not None:
                out.append((out[-1][0], None))  # break the line where x is unknown
            continue
        out.append((x_value, value))
    return out


def increasing(points: list[Point]) -> bool:
    """x rises strictly from point to point (what a line graph against time-like x needs)."""
    xs = [p[0] for p in points]
    return all(b > a for a, b in zip(xs, xs[1:])) or all(b >= a for a, b in zip(xs, xs[1:])) and _breaks_only(points)


def in_range(points: list[Point], rng: dict | None) -> list[Point]:
    if not rng:
        return list(points)
    return [p for p in points if rng["from"] <= p[0] <= rng["to"]]


def for_log(points: list[Point]) -> tuple[list[Point], int]:
    """Points ready for a log axis: values that are zero or negative become gaps, and are counted."""
    hidden = sum(1 for _, y in points if y is not None and y <= 0)
    return [(x, y if y is None or y > 0 else None) for x, y in points], hidden


def stats(points: list[Point]) -> dict:
    """Facts about a series, from every point (never from a downsampled copy)."""
    finite = [(x, y) for x, y in points if y is not None]
    gaps = [x for x, y in points if y is None]
    low = min(finite, key=lambda p: p[1], default=None)
    high = max(finite, key=lambda p: p[1], default=None)
    return {
        "points": len(points), "finite": len(finite), "from": points[0][0] if points else None,
        "to": points[-1][0] if points else None,
        "min": low[1] if low else None, "min_at": low[0] if low else None,
        "max": high[1] if high else None, "max_at": high[0] if high else None,
        "last": finite[-1][1] if finite else None, "last_at": finite[-1][0] if finite else None,
        "first_gap_at": gaps[0] if gaps else None,
    }


def align(base: list[Point], other: list[Point]) -> list[Point]:
    """other's values at base's x positions, within the range both cover. A base x between two of
    other's points gets the straight line between them, but only when both are real values; next
    to a gap it's a gap. Nothing outside the overlap is returned (no extrapolation). other's x must
    be increasing; otherwise nothing can be aligned and [] is returned."""
    if not base or not increasing(base) or len(other) < 1 or not increasing(other):
        return []
    xs = [p[0] for p in other]
    lo, hi = max(base[0][0], xs[0]) if base else 0, min(base[-1][0], xs[-1]) if base else 0
    out: list[Point] = []
    for bx, _ in base:
        if bx < lo or bx > hi:
            continue
        j = bisect.bisect_left(xs, bx)
        if j < len(xs) and xs[j] == bx:
            out.append((bx, other[j][1]))
            continue
        left, right = other[j - 1], other[j]
        if left[1] is None or right[1] is None:
            out.append((bx, None))
            continue
        t = fraction(bx, left[0], right[0])
        # A convex combination avoids overflowing right-left when finite
        # endpoints have opposite signs near the floating-point limit.
        out.append((bx, (1 - t) * left[1] + t * right[1]))
    return out


def compare(base: list[Point], other: list[Point]) -> dict | None:
    """How far other is from base where both have values: the largest absolute difference and
    where it is, on base's x positions inside the overlap. None when they don't overlap."""
    aligned = dict(align(base, other))
    pairs = [(x, y, aligned[x]) for x, y in base if x in aligned]
    if not pairs:
        return None
    both = [(x, abs(b - a)) for x, a, b in pairs if a is not None and b is not None]
    overflowed = [x for x, difference in both if not math.isfinite(difference)]
    worst = max(both, key=lambda p: p[1], default=None)
    measured = {x for x, _ in other}
    return {"from": pairs[0][0], "to": pairs[-1][0], "compared": len(both), "gaps": len(pairs) - len(both),
            "max_abs_diff": worst[1] if worst and not overflowed else None, "at": worst[0] if worst else None,
            "overflowed": len(overflowed),
            "interpolated": any(x not in measured for x, _, _ in pairs)}


def difference(base: list[Point], target: list[Point], relative: bool = False, threshold: float = 1e-12) -> dict:
    """Target minus baseline on the baseline grid, only inside their overlap.

    Relative differences divide by the baseline, retaining a gap when its absolute
    value is at or below the researcher's stored threshold. No gap is crossed.
    """
    aligned = dict(align(base, target))
    points, small, overflowed = [], 0, 0
    measured = {x for x, _ in target}
    interpolated = False
    for x, value in base:
        if x not in aligned:
            continue
        theirs = aligned[x]
        delta = None
        if value is not None and theirs is not None:
            if relative and abs(value) <= threshold:
                small += 1
            else:
                delta = theirs / value - 1 if relative else theirs - value
                if not math.isfinite(delta):
                    delta = None
                    overflowed += 1
                interpolated = interpolated or x not in measured
        points.append((x, delta))
    return {"points": points, "small_baseline": small, "overflowed": overflowed, "interpolated": interpolated}


def fraction(value: float, low: float, high: float) -> float:
    """Position in an interval without overflowing a finite wide interval."""
    span = high - low
    if math.isfinite(span):
        return (value - low) / span
    return (value / 2 - low / 2) / (high / 2 - low / 2)


def histogram_edges(values: list[float], bins: int) -> list[float]:
    """Common deterministic, equally spaced edges for one measure's finite values."""
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        width = max(abs(low) * .01, .5)
        low, high = low - width, high + width
        if not math.isfinite(low):
            low = min(values)
        if not math.isfinite(high):
            high = max(values)
    # Very close floating-point endpoints may not have bins+1 representable
    # values between them. Use distinct edges rather than zero-width bins.
    return list(dict.fromkeys((1 - i / bins) * low + (i / bins) * high for i in range(bins + 1)))


def histogram(points: list[Point], edges: list[float]) -> list[Point]:
    """Count actual finite values into common edges, including the upper endpoint."""
    if not edges:
        return []
    counts = [0] * (len(edges) - 1)
    for _, value in points:
        if value is None:
            continue
        index = max(0, min(len(counts) - 1, bisect.bisect_right(edges, value) - 1))
        counts[index] += 1
    return [((left / 2 + right / 2), count) for left, right, count in zip(edges, edges[1:], counts)]


def downsample(points: list[Point], limit: int = MAX_DISPLAY_POINTS) -> list[Point]:
    """At most about `limit` points for drawing. Each bucket keeps its lowest and highest value
    (in x order) and any gap, plus the first and last point, so the shape and every extreme stay."""
    if len(points) <= limit:
        return list(points)
    size = math.ceil(len(points) / max(1, limit // 2))
    out: list[Point] = []
    for start in range(0, len(points), size):
        bucket = points[start:start + size]
        keep = set()
        finite = [i for i, p in enumerate(bucket) if p[1] is not None]
        if finite:
            keep.update((min(finite, key=lambda i: bucket[i][1]), max(finite, key=lambda i: bucket[i][1])))
        gaps = [i for i, p in enumerate(bucket) if p[1] is None]
        if gaps:
            keep.update((gaps[0], gaps[-1]))
            # One gap between retained extrema is essential: keeping only the
            # first gap could otherwise connect values across a later NaN/Inf.
            extrema = sorted(keep)
            for left, right in zip(extrema, extrema[1:]):
                between = next((i for i in gaps if left < i < right), None)
                if between is not None:
                    keep.add(between)
        out += [bucket[i] for i in sorted(keep)]
    if out[0] != points[0]:
        out.insert(0, points[0])
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def _breaks_only(points: list[Point]) -> bool:
    """Repeated x is allowed only where xy() inserted a break (a gap at the previous x)."""
    return all(b[0] > a[0] or b[1] is None for a, b in zip(points, points[1:]))
