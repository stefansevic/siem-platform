"""
Plot 1: Precision, Recall, F1 grouped bar chart per detection rule.

Aggregates TP/FP/FN across all 76 runs per rule, recomputes metrics
on the totals, and produces 3 grouped bars per rule.

Usage:
    python experiments/plots/01_per_rule_metrics.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_runs, setup_style, ensure_output_dir


def main() -> None:
    setup_style()
    runs = load_runs()

    # Aggregate TP/FP/FN per rule across all runs
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    for run in runs:
        for rule, min_count in run.expected.items():
            if run.detected.get(rule, 0) >= min_count:
                totals[rule]["tp"] += 1
            else:
                totals[rule]["fn"] += 1
        for rule in run.detected:
            if rule not in run.expected:
                totals[rule]["fp"] += 1

    rules = sorted(totals.keys())
    precisions, recalls, f1s = [], [], []
    for rule in rules:
        tp, fp, fn = totals[rule]["tp"], totals[rule]["fp"], totals[rule]["fn"]
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        precisions.append(p)
        recalls.append(r)
        f1s.append(f1)

    x = np.arange(len(rules))
    width = 0.27

    fig, ax = plt.subplots()
    ax.bar(x - width, precisions, width, label="Precision", color="#4C72B0")
    ax.bar(x,         recalls,    width, label="Recall",    color="#55A868")
    ax.bar(x + width, f1s,        width, label="F1",        color="#C44E52")

    ax.set_ylabel("Score")
    ax.set_title("Detection metrics per rule (76 runs)")
    ax.set_xticks(x)
    ax.set_xticklabels(rules)
    ax.set_ylim(0, 1.1)
    ax.legend(loc="lower right")

    # Numerical labels above bars
    for i, (p, r, f) in enumerate(zip(precisions, recalls, f1s)):
        ax.text(i - width, p + 0.02, f"{p:.2f}", ha="center", fontsize=9)
        ax.text(i,         r + 0.02, f"{r:.2f}", ha="center", fontsize=9)
        ax.text(i + width, f + 0.02, f"{f:.2f}", ha="center", fontsize=9)

    out = ensure_output_dir() / "01_per_rule_metrics.png"
    plt.savefig(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()