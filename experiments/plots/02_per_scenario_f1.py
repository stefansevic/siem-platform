"""
Plot 2: Per-scenario F1 horizontal bar chart with error bars.

Shows mean F1 ± std for each of the 12 scenarios. Highlights where
the system performs perfectly versus where it fails by design
(nat_false_positive) or stochastically (slow_burst_brute_force).

Usage:
    python experiments/plots/02_per_scenario_f1.py
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_runs, setup_style, ensure_output_dir


def _f1(run) -> float:
    p = run.tp / (run.tp + run.fp) if (run.tp + run.fp) > 0 else 0.0
    r = run.tp / (run.tp + run.fn) if (run.tp + run.fn) > 0 else 0.0
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def main() -> None:
    setup_style()
    runs = load_runs()

    # Samo napadacki scenariji imaju smislen F1. Kontrolni scenariji
    # (bez ocekivanog napada) se mere brojem laznih alarma (tabela u radu i
    # matrica konfuzije), ne F1 merom - da se izbegne obmanjujuci F1=1.
    by_scenario: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        if not run.expected:
            continue
        by_scenario[run.scenario].append(_f1(run))

    # Sort: best F1 first, ties broken alphabetically
    items = sorted(by_scenario.items(), key=lambda kv: (-statistics.mean(kv[1]), kv[0]))
    scenarios = [k for k, _ in items]
    means = [statistics.mean(v) for _, v in items]
    stds = [statistics.stdev(v) if len(v) > 1 else 0.0 for _, v in items]

    fig, ax = plt.subplots(figsize=(9, 6))
    y = np.arange(len(scenarios))

    # Color: green if mean=1.0, orange if 0<mean<1, red if mean=0
    colors = []
    for m in means:
        if m >= 0.99:
            colors.append("#55A868")
        elif m > 0:
            colors.append("#DD8452")
        else:
            colors.append("#C44E52")

    ax.barh(y, means, xerr=stds, color=colors, height=0.65,
            error_kw={"ecolor": "black", "capsize": 3})
    ax.set_yticks(y)
    ax.set_yticklabels(scenarios)
    ax.set_xlabel("F1 score (mean ± std)")
    ax.set_title("Per-scenario F1 (attack scenarios)")
    ax.set_xlim(0, 1.1)
    ax.invert_yaxis()  # best on top

    for i, (m, s) in enumerate(zip(means, stds)):
        label = f"{m:.2f}" + (f" ± {s:.2f}" if s > 0 else "")
        ax.text(m + max(s, 0.02) + 0.01, i, label, va="center", fontsize=9)

    out = ensure_output_dir() / "02_per_scenario_f1.png"
    plt.savefig(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()