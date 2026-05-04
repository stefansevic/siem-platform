"""
Compute Precision, Recall, F1 for SIEM detection runs.

Reads ground-truth JSON files from experiments/runs/. Each file is
expected to contain both `expected` and `actual_incidents` (populated
by run_scenario.py at the end of each run, before the next reset).

Outputs:
    experiments/results/per_run.jsonl     — one line per run with full detail
    experiments/results/per_rule.csv      — Precision/Recall/F1 per rule
    experiments/results/per_scenario.csv  — mean ± std per scenario

Usage:
    python compute_metrics.py
    python compute_metrics.py --runs-dir experiments/runs --output-dir experiments/results
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


DEFAULT_RUNS_DIR = "experiments/runs"
DEFAULT_OUTPUT_DIR = "experiments/results"


@dataclass
class RunMetrics:
    run_id: str
    scenario: str
    started_at: str
    ended_at: str
    expected: dict[str, int]    # rule_name -> min_count
    detected: dict[str, int]    # rule_name -> count of incidents
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


def load_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Load every *.json under runs_dir. Skips files that fail to parse."""
    runs = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            with path.open() as fh:
                runs.append(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warn: skipping {path.name}: {exc}", file=sys.stderr)
    return runs


def compute_run_metrics(run: dict[str, Any]) -> RunMetrics:
    """Match expected vs actual_incidents at rule_name level with min_count."""
    expected: dict[str, int] = {}
    for exp in run.get("expected", []):
        rule = exp["rule"]
        expected[rule] = expected.get(rule, 0) + exp.get("min_count", 1)

    detected: dict[str, int] = defaultdict(int)
    for inc in run.get("actual_incidents", []):
        detected[inc["rule_name"]] += 1

    tp = 0
    fn = 0
    for rule, min_count in expected.items():
        observed = detected.get(rule, 0)
        if observed >= min_count:
            tp += 1
        else:
            fn += 1

    fp = 0
    for rule in detected:
        if rule not in expected:
            fp += 1

    # Negative scenarios (expected={}) with no detections are perfect:
    # treat absence of FP as Precision=1.0.
    if not expected:
        precision = 1.0 if fp == 0 else 0.0
        recall = 1.0  # nothing to miss
    else:
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return RunMetrics(
        run_id=run["run_id"],
        scenario=run["scenario"],
        started_at=run["started_at"],
        ended_at=run["ended_at"],
        expected=expected,
        detected=dict(detected),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def aggregate_per_rule(runs: list[RunMetrics]) -> list[dict[str, Any]]:
    """Sum TP/FP/FN per rule across all runs and recompute Precision/Recall/F1."""
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )

    for run in runs:
        for rule, min_count in run.expected.items():
            observed = run.detected.get(rule, 0)
            if observed >= min_count:
                totals[rule]["tp"] += 1
            else:
                totals[rule]["fn"] += 1
        for rule in run.detected:
            if rule not in run.expected:
                totals[rule]["fp"] += 1

    rows = []
    for rule, t in sorted(totals.items()):
        tp, fp, fn = t["tp"], t["fp"], t["fn"]
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        rows.append({
            "rule_name": rule,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
        })
    return rows


def aggregate_per_scenario(runs: list[RunMetrics]) -> list[dict[str, Any]]:
    """Compute mean and std of P/R/F1 per scenario across its runs."""
    by_scenario: dict[str, list[RunMetrics]] = defaultdict(list)
    for run in runs:
        by_scenario[run.scenario].append(run)

    rows = []
    for scenario, runs_for in sorted(by_scenario.items()):
        n = len(runs_for)
        precisions = [r.precision for r in runs_for]
        recalls = [r.recall for r in runs_for]
        f1s = [r.f1 for r in runs_for]
        rows.append({
            "scenario": scenario,
            "n_runs": n,
            "precision_mean": round(statistics.mean(precisions), 4),
            "precision_std": round(statistics.stdev(precisions), 4) if n > 1 else 0.0,
            "recall_mean": round(statistics.mean(recalls), 4),
            "recall_std": round(statistics.stdev(recalls), 4) if n > 1 else 0.0,
            "f1_mean": round(statistics.mean(f1s), 4),
            "f1_std": round(statistics.stdev(f1s), 4) if n > 1 else 0.0,
        })
    return rows


def write_jsonl(path: Path, runs: list[RunMetrics]) -> None:
    with path.open("w") as fh:
        for run in runs:
            fh.write(json.dumps(asdict(run)) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, type=Path)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, type=Path)
    args = parser.parse_args()

    if not args.runs_dir.is_dir():
        print(f"error: runs dir not found: {args.runs_dir}", file=sys.stderr)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    raw_runs = load_runs(args.runs_dir)
    if not raw_runs:
        print(f"error: no runs found in {args.runs_dir}", file=sys.stderr)
        return 1

    metrics = [compute_run_metrics(run) for run in raw_runs]

    per_run_path = args.output_dir / "per_run.jsonl"
    per_rule_path = args.output_dir / "per_rule.csv"
    per_scenario_path = args.output_dir / "per_scenario.csv"

    write_jsonl(per_run_path, metrics)
    write_csv(per_rule_path, aggregate_per_rule(metrics))
    write_csv(per_scenario_path, aggregate_per_scenario(metrics))

    print(f"Wrote {per_run_path}")
    print(f"Wrote {per_rule_path}")
    print(f"Wrote {per_scenario_path}")
    print(f"Processed {len(metrics)} runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())