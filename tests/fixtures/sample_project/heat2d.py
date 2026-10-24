"""Explicit 2D heat equation solver (SimGuard demo project).

Forward Euler in time, 5-point Laplacian, Dirichlet 0 walls.
Usage: python heat2d.py config.json > run.log
"""
import json
import math
import sys


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


def main(config_path):
    with open(config_path) as f:
        cfg = json.load(f)
    n, dx, dt, alpha = cfg["grid_points"], cfg["dx"], cfg["dt"], cfg["alpha"]
    r = alpha * dt / dx ** 2
    print(f"heat2d: grid {n}x{n}, dx={dx}, dt={dt}, alpha={alpha}, steps={cfg['steps']}")

    T = initial_field(n, cfg["initial_peak"])
    for k in range(1, cfg["steps"] + 1):
        new = step(T, r)
        residual = max(abs(a - b) for row_new, row in zip(new, T) for a, b in zip(row_new, row))
        T = new
        peak = max(abs(v) for row in T for v in row)
        if not math.isfinite(peak) or not math.isfinite(residual):
            print(f"step {k:5d}  max|T| = {peak:.4e}  residual = {residual:.4e}")
            print(f"ERROR: non-finite temperature at step {k}, stopping")
            return 1
        if k % cfg["log_every"] == 0:
            print(f"step {k:5d}  max|T| = {peak:.4e}  residual = {residual:.4e}")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "config.json"))
