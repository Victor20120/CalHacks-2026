"""The Visualization Studio: graphs of runs and experiments drawn by ExpandRG's toolkit, changed
by checked operations from the researcher or their own agent. CREAO and the connector are faked."""
import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.creao import ChatSettings
from backend.db.database import Database
from backend.orchestration.toolkit import ToolError
from backend.visualization import ops as graph_ops
from backend.visualization.figure import PALETTE_LIGHT, build as build_figure
from backend.visualization.spec import new_spec, normalized
from backend.visualization.sources import fingerprint
from backend.visualization.store import SavedGraphConflict, SavedGraphs
from backend.visualization.transforms import align, compare, difference, downsample, histogram, histogram_edges, stats, xy

EXPERIMENT = {"id": "e1", "set_id": "set1", "member_id": "researcher_a", "project_id": "heat-diffusion", "status": "finished",
              "label": "dt 0.03 to 0.02", "created_at": "2026-10-25T01:00:00+00:00", "started_at": "2026-10-25T01:00:05+00:00",
              "baseline": "run.log", "changes": {"dt": [0.03, 0.02]}}
DETAIL = {**EXPERIMENT, "metrics": ["residual"], "last_step": 380, "alerts": [],
          "series": {"residual": [[s, 1e-3 * 0.98 ** (s / 20)] for s in range(0, 400, 20)]},
          "baseline_result": {"metrics": ["residual"], "last_step": 280, "alerts": [{"severity": "critical", "step": 260, "metric": "residual"}],
                              "series": {"residual": [[s, 1e-3 * 1.2 ** (s / 20)] for s in range(0, 300, 20)]}}}


class Connector:
    """The local connector: experiments only, and it must never be asked to change anything."""

    def __init__(self):
        self.posts = []

    def get(self, path, params=None):
        if path == "/experiments":
            return [EXPERIMENT] if params == {"member_id": "researcher_a", "project_id": "heat-diffusion"} else []
        if path == "/experiments/e1":
            return json.loads(json.dumps(DETAIL))
        raise ToolError("not found")

    def post(self, path, body):
        self.posts.append(path)
        raise ToolError("no files in this test")


class Creao:
    def __init__(self):
        self.replies, self.calls = [], []

    def run(self, inputs):
        self.calls.append(inputs)
        return SimpleNamespace(reply=self.replies.pop(0) if self.replies else "Done.\n```json\n{\"graph_ops\": []}\n```",
                               run_id=f"run-{len(self.calls)}")


def ops_block(text, *ops):
    return text + "\n```json\n" + json.dumps({"graph_ops": list(ops)}) + "\n```"


@pytest.fixture
def lab(tmp_path):
    creao, connector = Creao(), Connector()
    data = tmp_path / "data"
    client = TestClient(create_app(client_factory=lambda prefix: creao, data_dir=data, connector=connector,
                                   chat_setup=lambda: ChatSettings("CHAT", "test")))
    headers = {m: {"Authorization": f"Bearer {client.post('/sign-in', json={'member_id': m}).json()['token']}"}
               for m in ("researcher_a", "researcher_b", "leader")}
    lab = SimpleNamespace(client=client, creao=creao, connector=connector, headers=headers, db=Database(data))
    lab.fast = run(lab, "researcher_a", "dt = 0.03", range(0, 410, 10), lambda i: 1e-3 * 1.05 ** i,
                   nan_at=300, alerts=[("critical", 300, "residual"), ("warning", 200, "residual")], energy=True)
    lab.slow = run(lab, "researcher_a", "dt = 0.02", range(0, 400, 15), lambda i: 1e-3 * 0.99 ** i)
    lab.other = run(lab, "researcher_b", "B's own run", range(0, 100, 10), lambda i: 1.0)
    return lab


def run(lab, member, label, steps, residual, nan_at=None, alerts=(), energy=False):
    run_id = lab.client.post("/simulation-runs", json={"project_id": "heat-diffusion", "run_label": label},
                             headers=lab.headers[member]).json()["id"]
    points = [{"step": s, "values": {"residual": None if s == nan_at else residual(i),
                                     **({"energy": 10 - i} if energy else {})}} for i, s in enumerate(steps)]
    reported = lab.client.post(f"/simulation-runs/{run_id}/report", headers=lab.headers[member], json={
        "points": points, "alerts": [{"severity": s, "check": "nan_inf" if s == "critical" else "sudden_jump",
                                      "message": f"{s} at {step}", "metric": m, "step": step} for s, step, m in alerts]})
    assert reported.status_code == 200, reported.text
    return f"run:{run_id}"


def act(lab, spec, *ops, member="researcher_a"):
    answer = lab.client.post("/visualize/ops", json={"spec": spec, "ops": list(ops)}, headers=lab.headers[member])
    assert answer.status_code == 200, answer.text
    return answer.json()


def overlay(lab):
    return act(lab, new_spec("heat-diffusion"), {"op": "create_graph", "datasets": [lab.fast, lab.slow], "metrics": ["residual"]})


def test_the_catalog_lists_your_runs_and_experiments_and_nobody_elses(lab):
    mine = lab.client.get("/visualize/datasets?project_id=heat-diffusion", headers=lab.headers["researcher_a"]).json()
    ids = {d["id"]: d for d in mine["datasets"]}
    assert set(ids) == {lab.fast, lab.slow, "exp:e1", "base:set1"}
    assert ids["exp:e1"]["parameters"] == {"dt": 0.02} and ids["base:set1"]["parameters"] == {"dt": 0.03}
    assert ids[lab.fast]["alerts"] == {"critical": 1, "warning": 1}
    leaders = {d["id"] for d in lab.client.get("/visualize/datasets?project_id=heat-diffusion", headers=lab.headers["leader"]).json()["datasets"]}
    assert {lab.fast, lab.slow, lab.other} <= leaders and "exp:e1" not in leaders  # the leader sees shared runs, not A's experiments
    assert lab.client.get("/visualize/datasets?project_id=wave-propagation", headers=lab.headers["researcher_b"]).status_code == 403


def test_runs_overlay_with_colours_that_stay_with_each_run(lab):
    drawn = overlay(lab)
    traces = drawn["figure"]["data"]
    assert [t["name"] for t in traces] == ["dt = 0.03", "dt = 0.02"]
    assert [t["line"]["color"] for t in traces] == [PALETTE_LIGHT[0], PALETTE_LIGHT[1]]
    assert drawn["figure"]["layout"]["showlegend"] is True
    assert sum(a.get("meta") == "direct-label" for a in drawn["figure"]["layout"]["annotations"]) == 2
    assert None in traces[0]["y"]  # the NaN step stays a gap
    assert drawn["comparisons"][0]["metric"] == "residual" and drawn["comparisons"][0]["max_abs_diff"] is not None

    without_first = act(lab, drawn["spec"], {"op": "remove_run", "dataset": lab.fast})
    assert [t["line"]["color"] for t in without_first["figure"]["data"]] == [PALETTE_LIGHT[1]]  # not repainted
    assert without_first["figure"]["layout"]["showlegend"] is False  # one series: the title names it


def test_alerts_are_marked_and_a_range_that_hides_one_says_so(lab):
    drawn = overlay(lab)
    markers = {(s["meta"], s["x0"]) for s in drawn["figure"]["layout"]["shapes"]}
    assert markers == {("critical", 300), ("warning", 200)}
    # Two runs in one panel: labels would collide near the start, so the notes say whose lines they are.
    assert not any(a.get("meta") == "marker-label" for a in drawn["figure"]["layout"]["annotations"])
    assert "dt = 0.03, Critical at step 300, Warning at step 200." in " ".join(drawn["notes"])
    alone = act(lab, drawn["spec"], {"op": "remove_run", "dataset": lab.slow})
    assert sorted(a["text"] for a in alone["figure"]["layout"]["annotations"] if a.get("meta") == "marker-label") == \
        ["Critical · 300", "Warning · 200"]
    narrowed = act(lab, drawn["spec"], {"op": "set_time_range", "from": 0, "to": 250})
    assert any("hides the first Critical alert of dt = 0.03, at step 300" in n for n in narrowed["notes"])
    assert max(narrowed["figure"]["data"][0]["x"]) <= 250
    assert narrowed["stats"][0]["to"] <= 250


def test_a_log_scale_counts_the_values_it_cannot_show(lab):
    drawn = act(lab, new_spec("heat-diffusion"), {"op": "create_graph", "datasets": [lab.fast], "metrics": ["energy"]},
                {"op": "change_axis", "y_scale": "log"})
    assert any("31 energy values at or below zero can't be shown on a log scale" in n for n in drawn["notes"])
    assert drawn["stats"][0]["min"] == -30  # statistics still use every value


def test_refused_operations_change_nothing_and_say_why(lab):
    start = overlay(lab)
    result = act(lab, start["spec"],
                 {"op": "exec", "code": "import os"},
                 {"op": "add_run", "dataset": lab.other},
                 {"op": "change_metric", "metrics": ["pressure"]},
                 {"op": "update_style", "color": "red"},
                 {"op": "set_time_range", "from": 9, "to": 3},
                 {"op": "change_graph_type", "type": "3d"},
                 {"op": "configure_histogram", "bins": 0})
    reasons = [r["reason"] for r in result["refused"]]
    assert len(reasons) == 7 and not result["applied"]
    assert 'There is no operation "exec"' in reasons[0]
    assert "isn't one of the runs you can graph" in reasons[1]
    assert "No run on the graph has pressure" in reasons[2]
    assert "doesn't take color" in reasons[3] and "from < to" in reasons[4] and "integer from 1 to 200" in reasons[6]
    assert result["spec"] == start["spec"]

    labelled = act(lab, start["spec"], {"op": "update_style", "title": "<script>alert(1)</script> residual"})
    assert labelled["figure"]["layout"]["title"]["text"] == "&lt;script&gt;alert(1)&lt;/script&gt; residual"


def test_only_the_researcher_sets_units_and_a_scatter_shows_at_most_three_runs(lab):
    catalog = lab.client.get("/visualize/datasets?project_id=heat-diffusion", headers=lab.headers["researcher_a"]).json()["datasets"]
    spec = graph_ops.apply(new_spec("heat-diffusion"), [{"op": "create_graph", "datasets": [lab.fast], "metrics": ["residual"]}],
                           catalog)["spec"]
    by_agent = graph_ops.apply(spec, [{"op": "set_units", "measure": "residual", "unit": "K"}], catalog, actor="agent")
    assert by_agent["refused"] and "Only the researcher sets units" in by_agent["refused"][0]["reason"]
    by_researcher = act(lab, spec, {"op": "set_units", "measure": "residual", "unit": "K"})
    assert by_researcher["figure"]["layout"]["yaxis"]["title"]["text"] == "residual (K)"

    four = act(lab, spec, {"op": "select_runs", "datasets": [lab.fast, lab.slow, "exp:e1", "base:set1"]},
               {"op": "change_graph_type", "type": "scatter"})
    assert "at most 3 runs" in four["refused"][0]["reason"] and four["spec"]["type"] == "line"


def test_line_ends_too_large_to_place_are_left_to_the_legend():
    spec = {**new_spec("heat-diffusion"), "datasets": ["a", "b"], "colors": {"a": 0, "b": 1}, "metrics": ["T"]}
    datasets = {d: {"id": d, "label": d, "series": {"T": [(0, 1.0), (10, end)]}, "alerts": [], "thinned": []}
                for d, end in (("a", 1e304), ("b", 5.0))}  # a blew up near the float limit
    def labels(s):
        return [a["text"] for a in build_figure(s, datasets)["figure"]["layout"]["annotations"] if a.get("meta") == "direct-label"]
    assert labels(spec) == ["b"]
    assert labels({**spec, "y_scale": "log"}) == ["a", "b"]  # on a log axis the position is about 304


def test_alignment_stays_in_the_overlap_and_never_crosses_a_gap():
    base = [(x, float(x)) for x in range(0, 101, 10)]
    other = [(x, 2.0 * x) for x in range(25, 151, 25)]
    aligned = dict(align(base, other))
    assert min(aligned) == 30 and max(aligned) == 100  # inside the overlap only, nothing extrapolated
    assert aligned[50] == 100.0 and aligned[30] == pytest.approx(60.0)  # measured, then interpolated
    with_gap = [(25, 50.0), (50, None), (75, 150.0)]
    assert dict(align(base, with_gap)) == {30: None, 40: None, 50: None, 60: None, 70: None}  # next to a gap: a gap
    result = compare(base, other)
    assert result["from"] == 30 and result["to"] == 100 and result["interpolated"] is True


def test_display_downsampling_keeps_every_spike_and_statistics_use_all_points():
    points = [(x, 1.0) for x in range(20_000)]
    points[12_345] = (12_345, 1e9)
    points[777] = (777, None)
    drawn = downsample(points, limit=500)
    assert len(drawn) < 1_600 and (12_345, 1e9) in drawn and (777, None) in drawn
    assert drawn[0] == points[0] and drawn[-1] == points[-1]
    assert stats(points)["max"] == 1e9 and stats(points)["first_gap_at"] == 777


def test_a_saved_graph_reopens_says_when_its_data_changed_and_can_be_undone(lab):
    drawn = overlay(lab)
    saved = lab.client.post("/visualizations", json={"title": "Residual, both time steps", "spec": drawn["spec"]},
                            headers=lab.headers["researcher_a"]).json()
    again = lab.client.get(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_a"]).json()
    assert again["figure"] == saved["figure"] and not any("changed" in n for n in again["notes"])

    narrowed = act(lab, saved["spec"], {"op": "set_time_range", "from": 0, "to": 200})
    lab.client.put(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_a"],
                   json={"spec": narrowed["spec"], "ops": narrowed["applied"]})
    run_id = lab.slow.split(":", 1)[1]
    lab.client.post(f"/simulation-runs/{run_id}/report", headers=lab.headers["researcher_a"],
                    json={"points": [{"step": 999, "values": {"residual": 5.0}}]})
    reopened = lab.client.get(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_a"]).json()
    assert reopened["notes"][0].startswith("The data behind this graph changed since it was saved")
    assert reopened["spec"]["range"] == {"from": 0, "to": 200}
    assert [h["actor"] for h in reopened["history"]] == ["researcher", "researcher"]

    undone = lab.client.post(f"/visualizations/{saved['id']}/undo", headers=lab.headers["researcher_a"]).json()
    assert undone["spec"]["range"] is None
    assert lab.client.get(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_b"]).status_code == 404  # private
    assert lab.client.get("/visualizations?project_id=heat-diffusion", headers=lab.headers["researcher_b"]).json() == []


def test_edit_with_agent_applies_checked_operations_and_sends_refusals_back_once(lab):
    spec = overlay(lab)["spec"]
    saved = lab.client.post("/visualizations", json={"title": "Residual", "spec": spec}, headers=lab.headers["researcher_a"]).json()
    lab.creao.replies = [
        ops_block("Narrowed to the blow-up and added the experiment.", {"op": "set_time_range", "from": 150, "to": 400},
                  {"op": "add_run", "dataset": "exp:nope"}, {"op": "set_units", "measure": "residual", "unit": "K"}),
        ops_block("Added the experiment by its real ID.", {"op": "add_run", "dataset": "exp:e1"}),
    ]
    started = lab.client.post(f"/visualizations/{saved['id']}/agent", json={"message": "Zoom in on the blow-up and add my experiment"},
                              headers=lab.headers["researcher_a"])
    assert started.status_code == 202
    job = lab.client.get(f"/jobs/{started.json()['job_id']}", headers=lab.headers["researcher_a"]).json()
    result = job["diagnosis"]
    assert job["status"] == "completed" and len(lab.creao.calls) == 2  # one corrected try, no more
    assert [o["op"] for o in result["applied"]] == ["set_time_range", "add_run"]
    assert result["spec"]["range"] == {"from": 150, "to": 400} and "exp:e1" in result["spec"]["datasets"]
    assert result["spec"]["units"] == {} and result["refused"] == []
    assert "refused some of your graph_ops" in lab.creao.calls[1]["observed_symptom"]
    assert "Only the researcher sets units" in lab.creao.calls[1]["observed_symptom"]

    sent = json.loads(lab.creao.calls[0]["observed_symptom"].rsplit("\n\n", 1)[1])["graph"]
    assert {d["id"] for d in sent["catalog"]} == {lab.fast, lab.slow, "exp:e1", "base:set1"}  # never B's run
    assert all(len(s["summary"]["points"]) <= 41 for s in sent["series"])  # summaries, never the full series
    assert sent["series"][0]["stats"]["first_gap_at"] == 300 and sent["comparisons"]
    assert "graph_ops" in lab.creao.calls[0]["output_format"] and "share" not in lab.creao.calls[0]["output_format"]

    reopened = lab.client.get(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_a"]).json()
    assert reopened["history"][-1]["actor"] == "agent" and reopened["history"][-1]["creao_run_id"] == "run-2"
    assert reopened["conversation"][-1]["reply"].startswith("Added the experiment")


def test_studio_calls_never_change_run_data_or_experiments(lab):
    def snapshot():
        with closing(lab.db.shared()) as conn:
            return (conn.execute("SELECT * FROM run_points ORDER BY rowid").fetchall(),
                    conn.execute("SELECT * FROM run_alerts ORDER BY rowid").fetchall())
    before = [list(map(tuple, rows)) for rows in snapshot()]
    drawn = overlay(lab)
    saved = lab.client.post("/visualizations", json={"title": "x", "spec": drawn["spec"]}, headers=lab.headers["researcher_a"]).json()
    act(lab, drawn["spec"], {"op": "set_time_range", "from": 0, "to": 100}, {"op": "change_axis", "y_scale": "log"})
    lab.creao.replies = [ops_block("These values look wrong; I checked the run instead of changing the graph.")]
    started = lab.client.post(f"/visualizations/{saved['id']}/agent", json={"message": "The results look wrong"},
                              headers=lab.headers["researcher_a"])
    lab.client.get(f"/jobs/{started.json()['job_id']}", headers=lab.headers["researcher_a"])
    assert [list(map(tuple, rows)) for rows in snapshot()] == before
    assert lab.connector.posts == []  # the connector was only ever read
    assert DETAIL["series"]["residual"][0] == [0, 1e-3]


def test_the_leader_graphs_by_hand_but_has_no_agent_to_edit_with(lab):
    drawn = act(lab, new_spec("heat-diffusion"), {"op": "create_graph", "datasets": [lab.other], "metrics": ["residual"]},
                member="leader")
    assert drawn["figure"]["data"][0]["name"] == "B's own run"
    saved = lab.client.post("/visualizations", json={"title": "B", "spec": drawn["spec"]}, headers=lab.headers["leader"]).json()
    refused = lab.client.post(f"/visualizations/{saved['id']}/agent", json={"message": "hi"}, headers=lab.headers["leader"])
    assert refused.status_code == 403 and "no active personal agent" in refused.json()["detail"]


@pytest.mark.parametrize("style", [[], "wide", 7])
def test_malformed_graph_styles_are_refused_instead_of_crashing(lab, style):
    response = lab.client.post("/visualize/figure", headers=lab.headers["researcher_a"],
                               json={"spec": {**new_spec("heat-diffusion"), "style": style}})
    assert response.status_code == 422
    assert "style must be an object" in response.json()["detail"]


def test_repeated_undo_walks_back_and_a_new_edit_branches_from_the_restored_state(lab):
    drawn = overlay(lab)
    saved = lab.client.post("/visualizations", headers=lab.headers["researcher_a"],
                            json={"title": "Undo history", "spec": drawn["spec"]}).json()
    for end in (300, 200, 100):
        drawn = act(lab, drawn["spec"], {"op": "set_time_range", "from": 0, "to": end})
        lab.client.put(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_a"],
                       json={"spec": drawn["spec"], "ops": drawn["applied"]})
    for expected in (200, 300):
        undone = lab.client.post(f"/visualizations/{saved['id']}/undo", headers=lab.headers["researcher_a"]).json()
        assert undone["spec"]["range"]["to"] == expected
    branch = act(lab, undone["spec"], {"op": "change_axis", "y_scale": "log"})
    lab.client.put(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_a"],
                   json={"spec": branch["spec"], "ops": branch["applied"]})
    for expected in ({"from": 0, "to": 300}, None):
        undone = lab.client.post(f"/visualizations/{saved['id']}/undo", headers=lab.headers["researcher_a"]).json()
        assert undone["spec"]["range"] == expected
        assert undone["spec"]["y_scale"] == "linear"
    assert lab.client.post(f"/visualizations/{saved['id']}/undo", headers=lab.headers["researcher_a"]).status_code == 409


def test_missing_x_samples_and_downsampling_cannot_bridge_gaps():
    points = xy({"T": [(0, 5), (1, 7), (2, 9)], "time": [(0, 0), (2, 2)]}, "T", "time")
    assert points == [(0, 5), (0, None), (2, 9)]
    original = [(x, float(x % 10)) for x in range(100)]
    original[0] = (0, 5.)
    original[2] = (2, -10.)
    original[4] = (4, 100.)
    original[1] = (1, None)
    original[3] = (3, None)
    drawn = downsample(original, limit=20)
    # Every retained finite-to-finite segment contains no source gap.
    for left, right in zip(drawn, drawn[1:]):
        if left[1] is not None and right[1] is not None:
            assert not any(original[i][1] is None for i in range(left[0] + 1, right[0]))


def test_extreme_finite_data_remain_json_serializable_and_comparisons_report_overflow():
    assert align([(5., 0.)], [(0., -1e308), (10., 1e308)]) == [(5., 0.)]
    assert align([(0., 0.)], [(-1e308, 0.), (1e308, 10.)]) == [(0., 5.)]
    assert compare([(0, 1e308)], [(0, -1e308)])["max_abs_diff"] is None
    spec = normalized({**new_spec("heat-diffusion"), "datasets": ["a", "b"], "metrics": ["T"]})
    datasets = {d: {"id": d, "label": d, "series": {"T": [(0, value), (1, value)]}, "alerts": [], "thinned": []}
                for d, value in (("a", 1e308), ("b", -1e308))}
    figure = build_figure(spec, datasets)
    json.dumps(figure, allow_nan=False)
    assert figure["comparisons"][0]["overflowed"] == 2
    assert any("exceed the numeric range" in note for note in figure["notes"])


def test_difference_plots_use_only_the_overlap_and_keep_small_baselines_as_gaps():
    baseline = [(0, 1.), (10, 0.), (20, None), (30, 4.), (40, 8.)]
    target = [(5, 3.), (15, 5.), (25, 7.), (35, 9.), (45, 11.)]
    absolute = difference(baseline, target)
    assert absolute["points"] == [(10, 4.), (20, None), (30, 4.), (40, 2.)]
    assert absolute["interpolated"] is True
    relative = difference(baseline, target, relative=True, threshold=.01)
    assert relative["points"] == [(10, None), (20, None), (30, 1.), (40, .25)]
    assert relative["small_baseline"] == 1


def test_phase_two_difference_controls_draw_derived_lines_and_remain_undoable(lab):
    start = overlay(lab)
    result = act(lab, start["spec"], {"op": "set_baseline", "dataset": lab.slow},
                 {"op": "compare_runs", "mode": "difference", "difference": "relative", "threshold": 1e-9})
    assert not result["refused"]
    assert result["spec"]["baseline"] == lab.slow and result["spec"]["layout"] == "difference"
    assert len(result["figure"]["data"]) == 1
    trace = result["figure"]["data"][0]
    assert trace["line"]["dash"] == "dash" and trace["meta"]["derived"] == "difference"
    assert trace["meta"]["dataset"] == lab.fast
    assert result["figure"]["layout"]["yaxis"]["title"]["text"] == "Relative difference in residual"
    assert all(x >= 0 and x <= 390 for x in trace["x"])
    assert any("Derived differences are target" in note for note in result["notes"])
    refused = act(lab, result["spec"], {"op": "remove_run", "dataset": lab.slow})
    assert refused["refused"] and refused["spec"] == result["spec"]


def test_histograms_use_shared_deterministic_bins_and_never_count_gaps(lab):
    result = act(lab, overlay(lab)["spec"], {"op": "configure_histogram", "bins": 5})
    assert result["spec"]["type"] == "histogram" and not result["refused"]
    first, second = result["figure"]["data"]
    assert first["type"] == second["type"] == "bar"
    assert first["x"] == second["x"] and first["customdata"] == second["customdata"]
    assert first["width"] == [right - left for left, right in first["customdata"]]
    assert first["width"] == second["width"]
    assert sum(first["y"]) == 40 and sum(second["y"]) == 27
    assert result["figure"]["layout"]["yaxis"]["title"]["text"] == "Count"
    assert result["stats"][0]["first_gap_at"] == 300
    assert result["comparisons"] == []
    assert histogram([(0, 0.), (1, 1.), (2, 2.), (3, None)], [0., 1., 2.]) == [(.5, 1), (1.5, 2)]
    edges = histogram_edges([-1e308, 1e308], 5)
    assert all(value == value and abs(value) != float("inf") for value in edges)


def test_parameter_sweeps_use_experiment_parameters_and_never_extrapolate(lab):
    result = act(lab, new_spec("heat-diffusion"),
                 {"op": "create_graph", "datasets": ["exp:e1", lab.fast], "metrics": ["residual"]},
                 {"op": "configure_sweep", "parameter": "dt", "step": 30})
    assert not result["refused"] and result["spec"]["type"] == "sweep"
    trace = next(t for t in result["figure"]["data"] if t["meta"].get("dataset") == "exp:e1")
    assert trace["x"] == [.02] and trace["marker"]["symbol"] == "diamond"
    expected = (DETAIL["series"]["residual"][1][1] + DETAIL["series"]["residual"][2][1]) / 2
    assert trace["y"] == [pytest.approx(expected)]
    assert any("not an experiment" in note for note in result["notes"])
    assert result["stats"][0]["at_step"] == 30 and result["stats"][0]["interpolated"] is True
    missing = act(lab, result["spec"], {"op": "configure_sweep", "parameter": "dt", "step": 1000})
    assert missing["figure"]["data"][0]["y"] == [None]
    assert any("without extrapolation" in note for note in missing["notes"])


def test_full_resolution_is_explicit_and_cannot_be_requested_by_the_agent(lab):
    catalog = lab.client.get("/visualize/datasets?project_id=heat-diffusion", headers=lab.headers["researcher_a"]).json()["datasets"]
    spec = overlay(lab)["spec"]
    allowed = graph_ops.apply(spec, [{"op": "set_resolution", "mode": "full"}], catalog)
    assert allowed["spec"]["resolution"] == "full"
    blocked = graph_ops.apply(spec, [{"op": "set_resolution", "mode": "full"}], catalog, actor="agent")
    assert blocked["refused"] and blocked["spec"]["resolution"] == "recorded"
    recreated = graph_ops.apply(allowed["spec"], [{"op": "create_graph", "datasets": [lab.fast]}], catalog)
    assert recreated["spec"]["resolution"] == "full"



def test_log_x_alert_labels_use_plotly_log_coordinates_and_explain_hidden_zero_steps():
    spec = normalized({**new_spec("heat-diffusion"), "datasets": ["a"], "metrics": ["T"], "x_scale": "log"})
    data = {"a": {"id": "a", "label": "Run A", "series": {"T": [(0, 1.), (10, 2.), (100, 3.)]}, "thinned": [],
                  "alerts": [{"severity": "critical", "step": 0, "metric": "T"}, {"severity": "warning", "step": 100, "metric": "T"}]}}
    result = build_figure(spec, data)
    labels = [a for a in result["figure"]["layout"]["annotations"] if a.get("meta") == "marker-label"]
    assert [(a["text"], a["x"]) for a in labels] == [("Warning · 100", 2.)]
    assert any("Critical alert" in note and "cannot be shown on a log x axis" in note for note in result["notes"])


def test_sweep_final_value_remains_a_gap_and_repeated_parameters_are_not_connected():
    spec = normalized({**new_spec("heat-diffusion"), "datasets": ["a", "b"], "metrics": ["T"],
                       "type": "sweep", "sweep": {"parameter": "dt", "step": None}})
    data = {d: {"id": d, "label": d, "source": "experiment", "parameters": {"dt": .02},
                "series": {"T": [(0, 1.), (10, final)]}, "thinned": [], "alerts": []}
            for d, final in (("a", None), ("b", 2.))}
    result = build_figure(spec, data)
    assert result["figure"]["data"][0]["y"] == [None]
    assert not any(t["meta"].get("aggregate") for t in result["figure"]["data"])
    assert any("same dt" in note for note in result["notes"])
    assert result["stats"][0]["last"] is None



def test_stale_agent_edits_cannot_overwrite_a_newer_manual_saved_graph(lab):
    drawn = overlay(lab)
    graphs = SavedGraphs(lab.db, "researcher_a")
    saved = graphs.create("heat-diffusion", "Concurrent graph", drawn["spec"], drawn["fingerprints"])
    manual = act(lab, saved["spec"], {"op": "set_time_range", "from": 0, "to": 200})
    graphs.update(saved["id"], manual["spec"], manual["fingerprints"], manual["applied"])
    agent = act(lab, saved["spec"], {"op": "change_axis", "y_scale": "log"})
    with pytest.raises(SavedGraphConflict, match="newer graph was kept"):
        graphs.update(saved["id"], agent["spec"], agent["fingerprints"], agent["applied"],
                      actor="agent", creao_run_id="stale-agent-run", expected_spec=saved["spec"])
    latest = graphs.get(saved["id"])
    assert latest["spec"] == manual["spec"]
    assert len(latest["history"]) == 2 and all(h["creao_run_id"] != "stale-agent-run" for h in latest["history"])


def test_tiny_scientific_changes_are_fingerprinted_and_reported_when_reopening(lab):
    assert fingerprint([(0, 1e-20)]) != fingerprint([(0, 2e-20)])
    dataset = run(lab, "researcher_a", "Tiny residual", [0, 1], lambda i: 1e-20)
    drawn = act(lab, new_spec("heat-diffusion"), {"op": "create_graph", "datasets": [dataset], "metrics": ["residual"]})
    saved = lab.client.post("/visualizations", headers=lab.headers["researcher_a"],
                            json={"title": "Tiny residual", "spec": drawn["spec"]}).json()
    run_id = dataset.split(":", 1)[1]
    lab.client.post(f"/simulation-runs/{run_id}/report", headers=lab.headers["researcher_a"],
                   json={"points": [{"step": 1, "values": {"residual": 2e-20}}]})
    reopened = lab.client.get(f"/visualizations/{saved['id']}", headers=lab.headers["researcher_a"]).json()
    assert any("changed since it was saved" in note for note in reopened["notes"])
    assert reopened["figure"]["data"][0]["y"][-1] == 2e-20
