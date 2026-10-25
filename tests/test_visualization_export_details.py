"""Publication output keeps the graph's coordinates, labels, and derived marks."""
from __future__ import annotations


import pytest

from backend.visualization.export import export_figure
from backend.visualization.figure import build as build_figure
from backend.visualization.spec import new_spec, normalized


@pytest.fixture
def inspect_export(monkeypatch):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    seen = []

    def save(figure, stream, **kwargs):
        snapshot = {"title": figure._suptitle.get_text(), "axes": []}
        for ax in figure.axes:
            snapshot["axes"].append({
                "title": ax.get_title(loc="left"), "xlabel": ax.get_xlabel(), "ylabel": ax.get_ylabel(),
                "lines": [(list(line.get_xdata()), list(line.get_ydata())) for line in ax.lines],
                "patch_widths": [patch.get_width() for patch in ax.patches],
                "collections": [{"vertices": collection.get_paths()[0].vertices.copy(),
                                 "offsets": collection.get_offsets().copy()} for collection in ax.collections],
                "annotations": [{"text": annotation.get_text(), "xy": annotation.xy,
                                 "data_coords": annotation.xycoords is ax.transData,
                                 "x_data_y_domain": annotation.xycoords is ax.get_xaxis_transform(),
                                 "x_domain_y_data": annotation.xycoords is ax.get_yaxis_transform(),
                                 "domain_coords": annotation.xycoords is ax.transAxes} for annotation in ax.texts],
            })
        seen.append(snapshot)
        stream.write(b"inspected publication figure")

    monkeypatch.setattr(Figure, "savefig", save)
    return seen


def test_annotation_domains_and_log_coordinates_survive_export(inspect_export):
    figure = {"data": [
        {"x": [0, 2], "y": [1, 2], "name": "First"},
        {"x": [1, 10], "y": [.01, .1], "name": "Second", "xaxis": "x2", "yaxis": "y2"},
    ], "layout": {
        "xaxis": {"type": "linear"}, "yaxis": {"domain": [.6, 1], "type": "linear"},
        "xaxis2": {"type": "log"}, "yaxis2": {"domain": [0, .4], "type": "log"},
        "annotations": [
            {"text": "Alert at the top", "xref": "x", "yref": "y domain", "x": 2, "y": 1},
            {"text": "A log data point", "xref": "x2", "yref": "y2", "x": 1, "y": -3},
            {"text": "A panel position", "xref": "x2 domain", "yref": "y2 domain", "x": .2, "y": .8},
        ],
        "shapes": [{"type": "line", "xref": "x2", "yref": "y2 domain", "x0": 5, "x1": 5}],
    }}
    export_figure(figure, format="svg")
    first, second = inspect_export[0]["axes"]
    annotation = first["annotations"][0]
    assert annotation["xy"] == (2, 1) and annotation["x_data_y_domain"]
    logarithmic, domain = second["annotations"]
    assert logarithmic["xy"] == pytest.approx((10, .001)) and logarithmic["data_coords"]
    assert domain["xy"] == (.2, .8) and domain["domain_coords"]
    # The first panel never receives the second panel's alert line.
    assert len(first["lines"]) == 1 and second["lines"][-1][0] == [5, 5]


def test_panel_titles_axis_labels_and_annotations_are_unescaped_once(inspect_export):
    figure = {"data": [
        {"x": [0, 1], "y": [1, 2], "name": "A &amp; B"},
        {"x": [0, 1], "y": [2, 1], "name": "C", "xaxis": "x2", "yaxis": "y2"},
    ], "layout": {
        "title": {"text": "Mass &amp; energy &lt;checked&gt;"},
        "xaxis": {"title": {"text": "Step &lt;n&gt;"}},
        "yaxis": {"domain": [.6, 1], "title": {"text": "A &amp; B"}},
        "xaxis2": {"title": "Time &lt;t&gt;"}, "yaxis2": {"domain": [0, .4]},
        "annotations": [
            {"text": "Top &amp; first", "xref": "paper", "yref": "paper", "x": 0, "y": .98, "meta": "panel-title"},
            {"text": "Bottom &lt;second&gt;", "xref": "paper", "yref": "paper", "x": 0, "y": .38, "meta": "panel-title"},
            {"text": "r &lt; 1 &amp; stable", "xref": "x", "yref": "y", "x": .5, "y": 1.5},
        ],
    }}
    export_figure(figure, format="svg")
    snapshot = inspect_export[0]
    first, second = snapshot["axes"]
    assert snapshot["title"] == "Mass & energy <checked>"
    assert (first["title"], second["title"]) == ("Top & first", "Bottom <second>")
    assert (first["xlabel"], first["ylabel"], second["xlabel"]) == ("Step <n>", "A & B", "Time <t>")
    assert first["annotations"][0]["text"] == "r < 1 & stable"


def test_histogram_publication_bars_keep_the_displayed_bin_widths(inspect_export):
    spec = normalized({**new_spec("research"), "datasets": ["exp:a"], "metrics": ["residual"],
                       "type": "histogram", "histogram": {"bins": 3}})
    datasets = {"exp:a": {"id": "exp:a", "label": "Experiment A", "source": "experiment", "parameters": {"dt": .01},
                           "series": {"residual": [(0, 10), (1, 30), (2, 50), (3, 70)]}, "alerts": [], "thinned": []}}
    built = build_figure(spec, datasets)
    trace = built["figure"]["data"][0]
    expected = [right - left for left, right in trace["customdata"]]
    assert expected == trace["width"] and all(width > 1 for width in expected)
    export_figure(built["figure"], format="svg")
    assert inspect_export[0]["axes"][0]["patch_widths"] == pytest.approx(expected)


def test_sweep_interpolation_keeps_its_diamond_in_publication_output(inspect_export):
    import numpy as np
    from matplotlib.markers import MarkerStyle

    spec = normalized({**new_spec("research"), "datasets": ["exp:a"], "metrics": ["residual"], "type": "sweep",
                       "sweep": {"parameter": "dt", "step": 5}})
    datasets = {"exp:a": {"id": "exp:a", "label": "Experiment A", "source": "experiment", "parameters": {"dt": .01},
                           "series": {"residual": [(0, 2), (10, 4)]}, "alerts": [], "thinned": []}}
    built = build_figure(spec, datasets)
    assert built["figure"]["data"][0]["marker"]["symbol"] == "diamond"
    export_figure(built["figure"], format="svg")
    mark = inspect_export[0]["axes"][0]["collections"][0]
    diamond = MarkerStyle("D")
    np.testing.assert_allclose(mark["vertices"], diamond.get_path().transformed(diamond.get_transform()).vertices)
    np.testing.assert_allclose(mark["offsets"], [[.01, 3]])


def test_publication_titles_are_plain_text_even_when_they_resemble_math():
    pytest.importorskip("matplotlib")
    figure = {"data": [{"x": [0, 1], "y": [1, 2], "name": "Research"}],
              "layout": {"title": {"text": r"Literal $\not_a_math_command$"}}}
    assert export_figure(figure, format="svg").startswith(b"<?xml")
