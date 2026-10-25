"""Atomic monitoring retries, overlap ordering and access revocation; no network."""
from contextlib import closing
import json

import pytest

from backend.db.database import Database
from backend.monitoring import runs
from backend.monitoring.alerts import InvalidAlert
from backend.workspaces.demo import seed_demo
from local_connector.reports import FrozenReports


ALERT = {"severity": "critical", "check": "nan_inf", "message": "x became nonfinite",
         "metric": "x", "step": 5}
POINTS = [{"step": 5, "values": {"x": 30.0}}]


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "data", seed=seed_demo)
    with closing(db.shared()) as conn:
        members = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM members")}
        yield db, conn, members


def start(conn, members, key="local-run"):
    return runs.create_run(conn, members["researcher_a"], "heat-diffusion", "solver.log", key)["id"]


def test_lost_start_ack_survives_a_new_connection(store):
    db, conn, members = store
    first = start(conn, members)

    with closing(db.shared()) as retry:
        assert start(retry, members) == first
        assert retry.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0] == 1
        assert retry.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 1


def test_start_key_is_scoped_to_owner_and_project_and_rejects_changed_request(store):
    _, conn, members = store
    first = start(conn, members)
    other_project = runs.create_run(conn, members["researcher_a"], "wave-propagation", "solver.log", "local-run")
    other_owner = runs.create_run(conn, members["researcher_b"], "heat-diffusion", "solver.log", "local-run")

    assert len({first, other_project["id"], other_owner["id"]}) == 3
    with pytest.raises(runs.ReportConflict):
        runs.create_run(conn, members["researcher_a"], "heat-diffusion", "different.log", "local-run")
    assert conn.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0] == 3


def test_unkeyed_starts_still_create_distinct_runs(store):
    _, conn, members = store
    assert start(conn, members, None) != start(conn, members, None)


def test_lost_report_ack_does_not_duplicate_samples_or_anomalies(store):
    db, conn, members = store
    run_id = start(conn, members)
    request = {"batch_id": "batch-1", "producer_id": "watcher-1", "source_revision": 1}
    ack = runs.add_report(conn, "researcher_a", run_id, POINTS, [ALERT], **request)
    before = runs.run_detail(conn, members["researcher_a"], run_id)

    with closing(db.shared()) as retry:
        assert runs.add_report(retry, "researcher_a", run_id, POINTS, [ALERT], **request) == ack
        after = runs.run_detail(retry, members["researcher_a"], run_id)
        assert after == before
        assert len(after["alerts"]) == 1
        assert after["series"] == {"x": [[5, 30.0]]}
        receipt = retry.execute("SELECT payload FROM context_events WHERE event_type = 'run.reported'").fetchone()[0]
        metadata = json.loads(receipt)
        assert metadata["anomaly_refs"] == [f"anomaly:{after['alerts'][0]['id']}"]
        assert "points" not in metadata and "alerts" not in metadata


@pytest.mark.parametrize("change", ["points", "alerts", "stopped", "source_revision"])
def test_changed_payload_cannot_reuse_a_report_key(store, change):
    _, conn, members = store
    run_id = start(conn, members)
    request = {"points": POINTS, "alerts": [ALERT], "stopped": False,
               "batch_id": "batch-1", "producer_id": "watcher-1", "source_revision": 1}
    runs.add_report(conn, "researcher_a", run_id, **request)
    before = runs.run_detail(conn, members["researcher_a"], run_id)
    replacements = {"points": [{"step": 5, "values": {"x": 40}}], "alerts": [],
                    "stopped": True, "source_revision": 2}

    with pytest.raises(runs.ReportConflict):
        runs.add_report(conn, "researcher_a", run_id, **{**request, change: replacements[change]})
    assert runs.run_detail(conn, members["researcher_a"], run_id) == before


def test_overlap_merges_run_metric_step_and_old_batches_cannot_clobber_new_values(store):
    _, conn, members = store
    run_id = start(conn, members)
    runs.add_report(conn, "researcher_a", run_id, POINTS, [], batch_id="newer", source_revision=3)
    runs.add_report(conn, "researcher_a", run_id,
                    [{"step": 5, "values": {"x": 39}}, {"step": 5, "values": {"x": 40}}], [],
                    batch_id="newest", source_revision=4)
    ack = runs.add_report(conn, "researcher_a", run_id,
                          [{"step": 4, "values": {"x": 20}}, {"step": 5, "values": {"x": 20}}], [],
                          stopped=True, batch_id="late-old-batch", source_revision=2)

    detail = runs.run_detail(conn, members["researcher_a"], run_id)
    assert detail["series"] == {"x": [[4, 20.0], [5, 40.0]]}
    assert detail["watching"] is True
    assert ack == {"points": 1, "alerts": 0}
    assert conn.execute("SELECT COUNT(*) FROM run_points").fetchone()[0] == 2
    runs.add_report(conn, "researcher_a", run_id, [], [], stopped=True,
                    batch_id="current-stop", source_revision=5)
    assert runs.get_visible_run(conn, members["researcher_a"], run_id)["watching"] is False


def test_legacy_overlap_also_keeps_a_single_observation(store):
    _, conn, members = store
    run_id = start(conn, members)
    runs.add_report(conn, "researcher_a", run_id, POINTS, [])
    runs.add_report(conn, "researcher_a", run_id, [{"step": 5, "values": {"x": 7}}], [])
    assert runs.run_detail(conn, members["researcher_a"], run_id)["series"] == {"x": [[5, 7.0]]}


def test_nonfinite_source_values_are_canonical_nulls_on_retry(store):
    _, conn, members = store
    run_id = start(conn, members)
    values = {"nan": float("nan"), "inf": float("inf"), "negative_inf": -float("inf"),
              "missing": None, "flag": True, "text": "invalid"}
    request = {"batch_id": "nonfinite", "source_revision": 1}
    first = runs.add_report(conn, "researcher_a", run_id, [{"step": 1, "values": values}], [], **request)
    second = runs.add_report(conn, "researcher_a", run_id,
                             [{"step": 1, "values": dict.fromkeys(values)}], [], **request)
    assert second == first
    assert all(series == [[1, None]] for series in runs.run_detail(conn, members["researcher_a"], run_id)["series"].values())


def test_report_and_receipt_roll_back_together(store, monkeypatch):
    _, conn, members = store
    run_id = start(conn, members)
    original = runs._save_receipt

    def unavailable(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("commit interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(runs, "_save_receipt", unavailable)
        with pytest.raises(RuntimeError, match="commit interrupted"):
            runs.add_report(conn, "researcher_a", run_id, POINTS, [ALERT], batch_id="retry")

    assert runs.run_detail(conn, members["researcher_a"], run_id)["series"] == {}
    assert conn.execute("SELECT COUNT(*) FROM run_alerts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM context_events WHERE event_type = 'run.reported'").fetchone()[0] == 0
    assert runs.add_report(conn, "researcher_a", run_id, POINTS, [ALERT], batch_id="retry") == {"points": 1, "alerts": 1}


def test_invalid_alert_rejects_the_whole_report_before_storage(store):
    _, conn, members = store
    run_id = start(conn, members)
    with pytest.raises(InvalidAlert):
        runs.add_report(conn, "researcher_a", run_id, POINTS, [{**ALERT, "severity": "panic"}], batch_id="bad")
    assert runs.run_detail(conn, members["researcher_a"], run_id)["series"] == {}
    assert conn.execute("SELECT COUNT(*) FROM context_events WHERE event_type = 'run.reported'").fetchone()[0] == 0


@pytest.mark.parametrize("viewer", ["researcher_a", "leader"])
@pytest.mark.parametrize("revoke", ["project", "member"])
def test_ownership_and_cached_member_objects_do_not_bypass_revocation(store, viewer, revoke):
    _, conn, members = store
    run_id = start(conn, members)
    runs.add_report(conn, "researcher_a", run_id, POINTS, [ALERT], batch_id="first")
    if revoke == "project":
        conn.execute("DELETE FROM project_members WHERE member_id = ? AND project_id = 'heat-diffusion'", (viewer,))
    else:
        conn.execute("UPDATE members SET status = 'offboarded' WHERE id = ?", (viewer,))
    conn.commit()

    assert runs.visible_runs(conn, members[viewer]) == []
    assert runs.get_visible_run(conn, members[viewer], run_id) is None
    assert runs.run_detail(conn, members[viewer], run_id) is None
    assert runs.recent_alerts(conn, members[viewer]) == []
    if viewer == "researcher_a":
        with pytest.raises(runs.RunNotFound):
            start(conn, members)  # even a duplicate start cannot reveal the old ACK
        with pytest.raises(runs.RunNotFound):
            runs.add_report(conn, viewer, run_id, POINTS, [ALERT], batch_id="first")


def test_nonowner_cannot_report_or_retrieve_an_ack(store):
    _, conn, members = store
    run_id = start(conn, members)
    runs.add_report(conn, "researcher_a", run_id, POINTS, [], batch_id="first")
    for member_id in ("researcher_b", "leader", "missing-member"):
        with pytest.raises(runs.RunNotFound):
            runs.add_report(conn, member_id, run_id, POINTS, [], batch_id="first")
    assert conn.execute("SELECT COUNT(*) FROM context_events WHERE event_type = 'run.reported'").fetchone()[0] == 1


def test_frozen_batch_survives_buffer_growth_mutation_and_restart():
    reports = FrozenReports()
    points, alerts = list(POINTS), [dict(ALERT)]
    first = reports.freeze(points, alerts)
    points.append({"step": 6, "values": {"x": 31}})
    alerts[0]["message"] = "changed after freeze"
    first["points"][0]["values"]["x"] = -1

    restored = FrozenReports.from_state(json.loads(json.dumps(reports.state())))
    retry = restored.freeze(points, alerts, stopped=True)
    assert retry["points"] == POINTS
    assert retry["alerts"] == [ALERT]
    assert retry["stopped"] is False
    assert restored.client_run_id == reports.client_run_id
    assert restored.acknowledge("unrelated-ack") is None
    assert restored.freeze(points, alerts) == retry
    assert restored.acknowledge(retry["batch_id"]) == (1, 1)

    next_batch = restored.freeze(points[1:], [], stopped=True)
    assert next_batch["batch_id"] != retry["batch_id"]
    assert next_batch["source_revision"] == retry["source_revision"] + 1
    assert next_batch["stopped"] is True
    assert next_batch["producer_id"] == retry["producer_id"]
    restored.new_run()
    assert restored.client_run_id != reports.client_run_id
    assert restored.freeze([], [])["source_revision"] == 1
