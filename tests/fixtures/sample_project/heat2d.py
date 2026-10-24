"""Explicit 2D heat equation solver (ExpandRG demo project).

Forward Euler in time, 5-point Laplacian, Dirichlet 0 walls.
Usage: python heat2d.py config.json [--delay SECONDS] > run.log
--delay pauses after each log line, so a run can be watched live.
"""
import json
import math
import sys
import time


def initial_field(n, peak):
    return [[peak * math.sin(math.pi * i / (n - 1)) * math.sin(math.pi * j / (n - 1)) for j in range(n)]
            for i in range(n)]


def step(T, r):
    n = len(T)
    new = [row[:] for row in T]
    for i in range(1, n - 1):
        for j in range(1, n - 1):
            lap = T[i + 1][j] + T[i - 1][j] + T[i][j + 1] + T[i][j - 1] - 4.0 * T[i][j]
            new[i][j] = T[i][j] + r * lap
    return new


def main(config_path, delay=0.0):
    with open(config_path) as f:
        cfg = json.load(f)
    n, dx, dt, alpha = cfg["grid_points"], cfg["dx"], cfg["dt"], cfg["alpha"]
    r = alpha * dt / dx ** 2

    def log(message):
        print(message, flush=True)
        time.sleep(delay)

    log(f"heat2d: grid {n}x{n}, dx={dx}, dt={dt}, alpha={alpha}, steps={cfg['steps']}")
    T = initial_field(n, cfg["initial_peak"])
    for k in range(1, cfg["steps"] + 1):
        new = step(T, r)
        residual = max(abs(a - b) for row_new, row in zip(new, T) for a, b in zip(row_new, row))
        T = new
        peak = max(abs(v) for row in T for v in row)
        if not math.isfinite(peak) or not math.isfinite(residual):
            log(f"step {k:5d}  max|T| = {peak:.4e}  residual = {residual:.4e}")
            log(f"ERROR: non-finite temperature at step {k}, stopping")
            return 1
        if k % cfg["log_every"] == 0:
            log(f"step {k:5d}  max|T| = {peak:.4e}  residual = {residual:.4e}")
    log("done")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    delay = float(args[args.index("--delay") + 1]) if "--delay" in args else 0.0
    paths = [a for i, a in enumerate(args) if a != "--delay" and (i == 0 or args[i - 1] != "--delay")]
    sys.exit(main(paths[0] if paths else "config.json", delay))
