"""
Plot 4: Detection latency histogram.

Latency = time from incident.first_event_at to incident.detected_at.
Shows how fast the SIEM reacts to attacks across all true-positive
detections (76 runs). Useful for the "system performance" section
of Chapter 6.

Usage:
    python experiments/plots/04_latency_distribution.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_runs, setup_style, ensure_output_dir, _parse


def main() -> None:
    setup_style()
    runs = load_runs()

    latencies: list[float] = []
    for run in runs:
        for inc in run.incidents:
            t_first = _parse(inc["first_event_at"])
            t_det = _parse(inc["detected_at"])
            latencies.append((t_det - t_first).total_seconds())

    if not latencies:
        print("No incidents found; nothing to plot.")
        return

    median = statistics.median(latencies)
    mean = statistics.mean(latencies)
    p95 = sorted(latencies)[int(0.95 * len(latencies))] if len(latencies) > 1 else latencies[0]

    fig, ax = plt.subplots()
    ax.hist(latencies, bins=20, color="#4C72B0", edgecolor="black", alpha=0.85)
    ax.axvline(median, color="#C44E52", linestyle="--", linewidth=1.5,
               label=f"median = {median:.2f}s")
    ax.axvline(mean, color="#55A868", linestyle="--", linewidth=1.5,
               label=f"mean = {mean:.2f}s")
    ax.axvline(p95, color="#DD8452", linestyle=":", linewidth=1.5,
               label=f"p95 = {p95:.2f}s")

    ax.set_xlabel("Detection latency (seconds)")
    ax.set_ylabel("Number of incidents")
    ax.set_title(f"Detection latency distribution (n = {len(latencies)})")
    ax.legend()

    out = ensure_output_dir() / "04_latency_distribution.png"
    plt.savefig(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()