"""Publication exports render the checked on-screen traces, never regenerated data."""
from __future__ import annotations

import io
import html
import math
import threading

EXPORT_LOCK = threading.Lock()  # Matplotlib's font/cache state is shared.
FONTS = ("DejaVu Sans", "DejaVu Serif", "Liberation Sans")


class ExportUnavailable(Exception):
    pass


def export_figure(figure: dict, *, format: str = "pdf", width: float = 6.4, height: float = 4.8,
                  dpi: int = 300, font_size: float = 10, font_family: str = "DejaVu Sans") -> bytes:
    if format not in ("png", "svg", "pdf") or font_family not in FONTS:
        raise ValueError("Choose PNG, SVG or PDF and a supported font.")
    if not (2 <= width <= 20 and 2 <= height <= 20 and 72 <= dpi <= 600 and 6 <= font_size <= 28):
        raise ValueError("Export sizes are 2–20 inches, 72–600 DPI, and 6–28 point fonts.")
    try:
        from matplotlib.figure import Figure
        from matplotlib import rc_context
    except ImportError:
        raise ExportUnavailable("Publication export needs the optional package: pip install 'expandrg[export]'.") from None
    with EXPORT_LOCK, rc_context({"font.family": font_family, "font.size": font_size,
                                  "svg.fonttype": "none", "pdf.fonttype": 42, "text.parse_math": False}):
        fig = Figure(figsize=(width, height), dpi=dpi, facecolor="white", layout="constrained")
        layout = figure.get("layout", {})
        traces = figure.get("data", [])
        names = list(dict.fromkeys(t.get("yaxis", "y") for t in traces)) or ["y"]
        axes = {name: fig.add_subplot(len(names), 1, index + 1) for index, name in enumerate(names)}
        for trace in traces:
            ax = axes[trace.get("yaxis", "y")]
            xs = [math.nan if x is None else x for x in trace.get("x", [])]
            ys = [math.nan if y is None else y for y in trace.get("y", [])]
            line, marker = trace.get("line", {}), trace.get("marker", {})
            color = line.get("color") or marker.get("color") or "#3259bc"
            label = html.unescape(trace.get("name", ""))
            if trace.get("type") == "bar":
                ax.bar(xs, ys, width=trace.get("width", .8), color=color, label=label, alpha=.75)
            elif trace.get("mode") == "markers":
                symbols = marker.get("symbol", "circle")
                if not isinstance(symbols, list):
                    symbols = [symbols] * len(xs)
                for index, (x, y, symbol) in enumerate(zip(xs, ys, symbols)):
                    ax.scatter([x], [y], color=color, label=label if index == 0 else "_nolegend_", s=20,
                               marker={"diamond": "D", "square": "s", "circle": "o"}.get(symbol, "o"))
            else:
                ax.plot(xs, ys, color=color, label=label, linewidth=line.get("width", 2),
                        linestyle="--" if line.get("dash") in ("dash", "dot", "dashdot") else "-")
        for name, ax in axes.items():
            suffix = name[1:]
            xsettings, ysettings = layout.get("xaxis" + suffix, {}), layout.get("yaxis" + suffix, {})
            for settings, setter in ((xsettings, ax.set_xlabel), (ysettings, ax.set_ylabel)):
                title = settings.get("title", {})
                setter(html.unescape(title.get("text", "") if isinstance(title, dict) else title))
            if xsettings.get("type") == "log": ax.set_xscale("log")
            if ysettings.get("type") == "log": ax.set_yscale("log")
            for settings, setter in ((xsettings, ax.set_xlim), (ysettings, ax.set_ylim)):
                if settings.get("range"):
                    limits = settings["range"]
                    setter([10 ** value for value in limits] if settings.get("type") == "log" else limits)
            ax.grid(True, color="#e2e6ec", linewidth=.5)
            ax.set_facecolor("white")
            ax.ticklabel_format(style="plain", axis="both", useOffset=False) if (
                xsettings.get("type") != "log" and ysettings.get("type") != "log") else None
            handles, labels = ax.get_legend_handles_labels()
            if layout.get("showlegend") and handles:
                ax.legend(handles, labels, frameon=False, fontsize=font_size)
        for shape in layout.get("shapes", []):
            name = shape.get("yref", "y").split(" ")[0]
            ax = axes.get(name, next(iter(axes.values())))
            if shape.get("type") == "line" and shape.get("x0") == shape.get("x1"):
                ax.axvline(shape["x0"], color=shape.get("line", {}).get("color", "#a33"), linewidth=.8)
        for note in layout.get("annotations", []):
            text = html.unescape(note.get("text", ""))
            if note.get("meta") == "panel-title":
                name = min(axes, key=lambda name: abs(layout.get("yaxis" + name[1:], {}).get("domain", [0, 1])[1] - note.get("y", 1)))
                axes[name].set_title(text, loc="left", fontsize=font_size)
                continue
            xref, yref = note.get("xref", "x"), note.get("yref", "y")
            if xref == "paper" and yref == "paper":
                continue
            name = yref.split(" ")[0]
            ax = axes.get(name, next(iter(axes.values())))
            suffix = name[1:] if name.startswith("y") else ""
            x, y = note.get("x"), note.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            x_domain, y_domain = "domain" in xref or xref == "paper", "domain" in yref or yref == "paper"
            if not x_domain and layout.get("xaxis" + suffix, {}).get("type") == "log": x = 10 ** x
            if not y_domain and layout.get("yaxis" + suffix, {}).get("type") == "log": y = 10 ** y
            transform = ax.transAxes if x_domain and y_domain else (
                ax.get_yaxis_transform() if x_domain else ax.get_xaxis_transform() if y_domain else ax.transData)
            ax.annotate(text, (x, y), xycoords=transform, fontsize=font_size)
        title = layout.get("title", {})
        fig.suptitle(html.unescape(title.get("text", "") if isinstance(title, dict) else title))
        out = io.BytesIO()
        fig.savefig(out, format=format, dpi=dpi, facecolor="white")
        fig.clear()
        return out.getvalue()
