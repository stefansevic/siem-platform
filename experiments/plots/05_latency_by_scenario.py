"""
Plot 5: Detection latency boxplot grouped by scenario.

Compares how fast the SIEM detects attacks across different scenario
types (clean attack, attack with noise, mixed traffic). Reveals
whether legitimate background traffic slows detection down.

Only scenarios with at least one detected incident appear in the plot.

Usage:
    python experiments/plots/05_latency_by_scenario.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_runs, setup_style, ensure_output_dir, _parse


def main() -> None:
    setup_style()
    runs = load_runs()

    by_scenario: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        for inc in run.incidents:
            t_first = _parse(inc["first_event_at"])
            t_det = _parse(inc["detected_at"])
            by_scenario[run.scenario].append((t_det - t_first).total_seconds())

    if not by_scenario:
        print("No incidents found; nothing to plot.")
        return

    # Sort by median latency descending (slowest first)
    items = sorted(
        by_scenario.items(),
        key=lambda kv: -sorted(kv[1])[len(kv[1]) // 2],
    )
    labels = [k for k, _ in items]
    data = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    bp = ax.boxplot(
        data,
        vert=False,
        patch_artist=True,
        labels=labels,
        widths=0.6,
        medianprops={"color": "#C44E52", "linewidth": 2},
    )
    for patch in bp["boxes"]:
        patch.set_facecolor("#4C72B0")
        patch.set_alpha(0.7)

    # Annotate sample size in a fixed column to the right of the plot
    x_max = max(max(s) for s in data)
    ax.set_xlim(right=x_max * 1.1)
    for i, samples in enumerate(data, start=1):
        ax.text(x_max * 1.04, i, f"n={len(samples)}",
                va="center", fontsize=9, color="gray")

    ax.set_xlabel("Detection latency (seconds)")
    ax.set_title("Detection latency by scenario")

    # Legend explaining boxplot anatomy
    legend_text = (
        "Box = IQR (25th–75th percentile)   "
        "Red line = median   "
        "Whiskers = non-outlier extremes   "
        "○ = outlier"
    )
    fig.text(0.5, 0.01, legend_text, ha="center", fontsize=8,
             color="gray", style="italic")
    plt.subplots_adjust(bottom=0.13)
    out = ensure_output_dir() / "05_latency_by_scenario.png"
    plt.savefig(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()