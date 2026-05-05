"""
Plot 3: Aggregate confusion matrix across all 76 runs.

Each run contributes one outcome per (rule, run) pair: TP, FP, FN, or TN.
The 2x2 matrix sums these and shows them as a colored heatmap with
counts and percentages. A standard view for any binary classifier.

Usage:
    python experiments/plots/03_confusion_matrix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_runs, setup_style, ensure_output_dir


def main() -> None:
    setup_style()
    runs = load_runs()

    tp = sum(r.tp for r in runs)
    fp = sum(r.fp for r in runs)
    fn = sum(r.fn for r in runs)
    tn = sum(r.tn for r in runs)
    total = tp + fp + fn + tn

    matrix = np.array([
        [tp, fn],
        [fp, tn],
    ])

    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(matrix, cmap="Blues", aspect="equal")

    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(["Predicted: Attack", "Predicted: No attack"])
    ax.set_yticklabels(["Actual: Attack", "Actual: No attack"])
    ax.set_title(f"Confusion matrix across {total} (rule, run) outcomes")

    # Cell labels: count + percentage + label
    labels = [["TP", "FN"], ["FP", "TN"]]
    for i in range(2):
        for j in range(2):
            count = matrix[i, j]
            pct = 100 * count / total if total > 0 else 0
            color = "white" if count > matrix.max() / 2 else "black"
            ax.text(j, i, f"{labels[i][j]}\n{count}\n({pct:.1f}%)",
                    ha="center", va="center", color=color,
                    fontsize=14, fontweight="bold")

    plt.colorbar(im, ax=ax, fraction=0.04)

    out = ensure_output_dir() / "03_confusion_matrix.png"
    plt.savefig(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()