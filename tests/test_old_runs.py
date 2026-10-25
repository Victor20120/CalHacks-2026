import pytest

from local_connector.simulation_monitor.old_runs import find_runs, read_run


def test_swimmer_run_gives_distance_velocity_and_efficiency(tmp_path):
    # 2 points a frame, 2 frames a beat (nbeat = 1/(dt*omFac) = 20, nw = 10), 3 beats; the centre moves +x.
    (tmp_path / "setup.txt").write_text("      np =           2\n      nw =          10 (how often write x)\n")
    (tmp_path / "parameters.f90").write_text("real(dp),parameter :: dt =5.0d-5   ! time step\n"
                                             "real(dp),parameter :: omFac =1.0d+3   ! beat frequency\n")
    centres = [0.0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5]  # frames 0..6: beats end at frames 2, 4, 6
    (tmp_path / "fort.10").write_text("".join(f"{x - 0.5} 0.0 0.0\n{x + 0.5} 0.0 0.0\n" for x in centres))
    (tmp_path / "fort.80").write_text("0.9E-03\n0.2D-03\n0.4E-03\n")
    (tmp_path / "run.log").write_text("     z-drift/beat  -2.0E-02   steadiness    0.002\n" * 3)

    run = read_run(tmp_path, "swimmer")

    assert run.metrics == ["distance from start (body lengths)", "velocity (body lengths / beat)",
                           "efficiency (velocity^2 / power)"]  # the group's graphs, nothing else
    assert [s.step for s in run.samples] == [1, 2, 3]
    assert run.samples[0].values == {"distance from start (body lengths)": pytest.approx(0.2)}  # beat 1 starts from rest
    beat3 = run.samples[2].values
    assert beat3["distance from start (body lengths)"] == pytest.approx(0.5)
    assert beat3["velocity (body lengths / beat)"] == pytest.approx(0.2)
    assert beat3["efficiency (velocity^2 / power)"] == pytest.approx(0.2 ** 2 / (0.4e-3 / 1e-3))


def test_finished_fortran_run_becomes_graphs(tmp_path):
    run = tmp_path / "runs" / "bare_sine"
    run.mkdir(parents=True)
    (run / "run.log").write_text("\n".join([
        " SHORT-WAVE FILTER ON: 2-node zigzag  40.00 e-folds/beat (alpha 2.000E-05)",
        "     z-drift/beat  -2.077E-02   steadiness    0.002",
        "     z-drift/beat  -2.076E-02   steadiness    0.001",
        " elapsed seconds:   5954.69",
    ]))
    (run / "build.log").write_text("step 1 residual = 9\nstep 2 residual = 9\n")  # not the run's log
    (run / "power.f90").write_text("write(90,'(e23.15)') pBody            ! fort.90 : body-only power\n"
                                   "write(91,'(e23.15)') pFib\n")
    (run / "fort.90").write_text("  0.38E-01\n  0.20D-03\n")
    (run / "fort.91").write_text("  0.0\n  NaN\n")
    (run / "fort.10").write_text("0.0 0.0 0.5\n0.0 0.0 0.6\n")  # positions per node: not a series
    (run / "fort.100").write_text("1.0\n" * 30_000)  # unnamed and big: a value per node, left out

    found = read_run(run, "bare_sine")

    assert found.metrics == ["z-drift/beat", "steadiness", "body-only power (fort.90)", "pFib (fort.91)"]
    assert [s.step for s in found.samples] == [1, 2]
    assert found.samples[1].values == {"z-drift/beat": -0.02076, "steadiness": 0.001,
                                       "body-only power (fort.90)": 0.0002, "pFib (fort.91)": None}
    assert [(a.check, a.metric, a.step) for a in found.alerts] == [("nan_inf", "pFib (fort.91)", 2)]
    assert find_runs(tmp_path / "runs") == [run]
