"""Read-only local data, private diagnostic attachments, claims and publication exports."""
import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from test_visualization import lab, overlay
from backend.visualization.agent import check_claims
from backend.visualization.export import export_figure
from backend.visualization.diagnostics import diagnostic_spec
from local_connector.file_access.grants import GrantRegistry
from local_connector.visualization import local_data


def test_full_log_and_manifest_units_are_read_without_losing_gaps_or_changing_files(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    log = project / "run.log"
    log.write_text("step 0 residual = 1\nstep 1 residual = nan\nstep 2 residual = 0.1\n")
    (project / "expandrg.json").write_text(json.dumps({"units": {"residual": "1"}}))
    registry = GrantRegistry(tmp_path / "grants.json")
    registry.grant(project)
    before = {p.name: p.read_bytes() for p in project.iterdir()}
    answer = local_data(registry, "run.log", True)
    assert answer["units"] == {"residual": "1"}
    assert answer["series"]["residual"] == [[0, 1.0], [1, None], [2, .1]]
    assert {p.name: p.read_bytes() for p in project.iterdir()} == before
    with pytest.raises(ValueError):
        local_data(registry, "../secret.log", True)
    second = tmp_path / "another"
    second.mkdir()
    (second / "run.log").write_text("step 0 residual = 99\n")
    registry.grant(second)
    with pytest.raises(ValueError, match="ambiguous"):
        local_data(registry, "run.log", True)


def test_full_logs_replace_thinned_points_only_when_the_original_samples_match(lab):
    recorded = lab.client.post("/visualize/figure", json={"spec": overlay(lab)["spec"]},
                               headers=lab.headers["researcher_a"]).json()
    base_get = lab.connector.get
    def get(path, params=None):
        if path == "/visualize/local-data":
            run_id = params["run_id"]
            with closing(lab.db.shared()) as conn:
                points = [[r["step"], r["value"]] for r in conn.execute(
                    "SELECT step,value FROM run_points WHERE run_id=? AND metric='residual' ORDER BY step", (run_id,))]
            return {"units": {"residual": "1"}, "series": {"residual": points}}
        return base_get(path, params)
    lab.connector.get = get
    spec = {**recorded["spec"], "datasets": [lab.slow], "resolution": "full"}
    accepted = lab.client.post("/visualize/figure", json={"spec": spec}, headers=lab.headers["researcher_a"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["spec"]["units"]["residual"] == "1"
    lab.connector.get = lambda path, params=None: ({"series": {"residual": [[0, 999], [15, 888]]}}
                                                  if path == "/visualize/local-data" else base_get(path, params))
    refused = lab.client.post("/visualize/figure", json={"spec": spec}, headers=lab.headers["researcher_a"]).json()
    assert any("no longer matches" in note for note in refused["notes"])
    assert refused["stats"][0]["points"] == recorded["stats"][1]["points"]


def test_quantitative_claims_are_checked_against_the_referenced_statistics():
    built = {"stats": [{"dataset": "run:a", "metric": "residual", "max": 1e-20}]}
    claims = [{"dataset": "run:a", "metric": "residual", "stat": "max", "value": value}
              for value in (1e-20, 1e-10)]
    checked = check_claims("The maximum residual is 1e-20.", claims, built)
    assert [c["status"] for c in checked] == ["verified", "mismatch"]
    assert check_claims("Residual reached 42.", None, built)[0]["status"] == "unverified"
    assert check_claims("The run diverged.", None, built) == []


def test_a_critical_alert_does_not_hide_a_negative_measure_on_a_log_axis(lab):
    from test_visualization import run
    dataset = run(lab, "researcher_a", "Negative energy", range(0, 50, 10), lambda i: -(i + 1),
                  alerts=[("critical", 20, "residual")])
    with closing(lab.db.shared()) as conn:
        member = dict(conn.execute("SELECT * FROM members WHERE id='researcher_a'").fetchone())
        spec = diagnostic_spec(conn, member, "heat-diffusion", dataset.removeprefix("run:"), None)
    assert spec["y_scale"] == "linear"


@pytest.mark.parametrize("format,signature", [("pdf", b"%PDF"), ("svg", b"<?xml"), ("png", b"\x89PNG")])
def test_publication_exports_use_exactly_the_displayed_trace_data(lab, monkeypatch, format, signature):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure
    built = overlay(lab)
    expected = built["figure"]["data"]
    original = Figure.savefig
    seen = []
    def save(figure, *args, **kwargs):
        for ax in figure.axes:
            for line in ax.lines:
                if len(line.get_xdata()) > 2:
                    seen.append((list(line.get_xdata()), list(line.get_ydata())))
        return original(figure, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", save)
    result = export_figure(built["figure"], format=format)
    assert result.startswith(signature)
    assert len(seen) == len(expected)
    for (xs, ys), trace in zip(seen, expected):
        assert xs == trace["x"]
        assert [None if v != v else v for v in ys] == trace["y"]


def test_export_api_enforces_project_access_and_size_bounds(lab):
    spec = overlay(lab)["spec"]
    assert lab.client.post("/visualize/export", json={"spec": spec, "width": 999},
                           headers=lab.headers["researcher_a"]).status_code == 422
    spec["project_id"] = "wave-propagation"
    assert lab.client.post("/visualize/export", json={"spec": spec},
                           headers=lab.headers["researcher_b"]).status_code == 403


def test_alert_diagnosis_attaches_default_and_agent_graphs_to_its_private_draft(lab):
    issue = {"title": "Time step instability", "status": "suspected", "confidence": "high",
             "evidence": "The residual became non-finite.", "proposed_validation": "Reduce the time step."}
    operations = [{"op": "create_graph", "datasets": [lab.fast, lab.slow], "metrics": ["residual"]}]
    lab.creao.run = lambda inputs: SimpleNamespace(
        run_id="diagnosis-provider", reply="Reduce the time step.\n```json\n" + json.dumps({"graph_ops": operations}) + "\n```",
        issues=[issue], summary={}, analysis_ran=True)
    run_id = lab.fast.removeprefix("run:")
    response = lab.client.post("/diagnose", json={"project_id": "heat-diffusion", "run_id": run_id,
                                                  "symptom": "Residual became NaN."}, headers=lab.headers["researcher_a"])
    assert response.status_code == 202, response.text
    job = lab.client.get("/jobs/" + response.json()["job_id"], headers=lab.headers["researcher_a"]).json()
    assert job["status"] == "completed", job
    graphs = job["diagnosis"]["diagnostic_graphs"]
    assert len(graphs) == 2
    assert graphs[0]["spec"]["range"] == {"from": 240, "to": 360}
    assert graphs[0]["spec"]["y_scale"] == "log"
    drafts = lab.client.get("/drafts", headers=lab.headers["researcher_a"]).json()
    assert drafts[-1]["diagnostic_graphs"] == graphs
    for graph in graphs:
        assert lab.client.get("/visualizations/" + graph["id"], headers=lab.headers["researcher_b"]).status_code == 404
    rejected = lab.client.post("/diagnose", json={"project_id": "heat-diffusion", "run_id": run_id,
                                                   "symptom": "Show A's run."}, headers=lab.headers["researcher_b"])
    assert rejected.status_code == 422
