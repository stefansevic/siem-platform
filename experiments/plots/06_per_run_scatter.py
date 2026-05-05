"""
Plot 6: Per-run F1 scatter plot.

Each dot is one run. Shows the underlying distribution behind the
aggregate metrics in plot 1, including the stochastic spread on
slow_burst_brute_force and the consistent FP on nat_false_positive.

A more honest view than aggregated bars: makes it clear when 1.0
means "all 10 runs were perfect" versus "averaged out to 1.0".

Usage:
    python experiments/plots/06_per_run_scatter.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_runs, setup_style, ensure_output_dir


def _f1(run) -> float:
    if not run.expected:
        return 1.0 if run.fp == 0 else 0.0
    p = run.tp / (run.tp + run.fp) if (run.tp + run.fp) > 0 else 0.0
    r = run.tp / (run.tp + run.fn) if (run.tp + run.fn) > 0 else 0.0
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def main() -> None:
    setup_style()
    runs = load_runs()

    by_scenario: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        by_scenario[run.scenario].append(_f1(run))

    scenarios = sorted(by_scenario.keys())
    fig, ax = plt.subplots(figsize=(10, 6))

    rng = np.random.default_rng(42)  # reproducible jitter
    for x_pos, scenario in enumerate(scenarios):
        f1s = by_scenario[scenario]
        # Horizontal jitter so overlapping points are visible
        x_jitter = rng.uniform(-0.18, 0.18, size=len(f1s))
        xs = [x_pos + j for j in x_jitter]
        ax.scatter(xs, f1s, s=60, alpha=0.65, edgecolor="black",
                   color="#4C72B0", linewidth=0.5)

    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels(scenarios, rotation=35, ha="right")
    ax.set_ylabel("F1 score")
    ax.set_title("Per-run F1 by scenario (each dot = one run)")
    ax.set_ylim(-0.05, 1.1)
    ax.axhline(1.0, color="gray", linestyle=":", linewidth=1, alpha=0.5)

    out = ensure_output_dir() / "06_per_run_scatter.png"
    plt.savefig(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()