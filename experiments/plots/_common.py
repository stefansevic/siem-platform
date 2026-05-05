"""
Shared helpers for plot scripts.

Loads ground-truth runs, parses timestamps, computes per-run metrics,
and provides matplotlib styling defaults consistent across all plots.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt

EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = EXPERIMENTS_DIR / "runs"
PLOTS_OUTPUT_DIR = EXPERIMENTS_DIR / "results" / "plots"


@dataclass
class Run:
    """Single experimental run with computed classification metrics."""
    run_id: str
    scenario: str
    started_at: datetime
    ended_at: datetime
    expected: dict[str, int]    # rule_name -> min_count
    detected: dict[str, int]    # rule_name -> count
    incidents: list[dict]       # raw incident payloads
    tp: int
    fp: int
    fn: int
    tn: int                     # 1 if expected={} and detected={}, else 0


def _parse(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def load_runs() -> list[Run]:
    """Read every runs/*.json and compute classification counts per run."""
    runs: list[Run] = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        with path.open() as fh:
            d = json.load(fh)

        expected: dict[str, int] = {}
        for exp in d.get("expected", []):
            r = exp["rule"]
            expected[r] = expected.get(r, 0) + exp.get("min_count", 1)

        detected: dict[str, int] = defaultdict(int)
        for inc in d.get("actual_incidents", []):
            detected[inc["rule_name"]] += 1

        tp = sum(1 for r, mc in expected.items() if detected.get(r, 0) >= mc)
        fn = sum(1 for r, mc in expected.items() if detected.get(r, 0) < mc)
        fp = sum(1 for r in detected if r not in expected)
        tn = 1 if (not expected and not detected) else 0

        runs.append(Run(
            run_id=d["run_id"],
            scenario=d["scenario"],
            started_at=_parse(d["started_at"]),
            ended_at=_parse(d["ended_at"]),
            expected=expected,
            detected=dict(detected),
            incidents=d.get("actual_incidents", []),
            tp=tp,
            fp=fp,
            fn=fn,
            tn=tn,
        ))
    return runs


def setup_style() -> None:
    """Consistent academic-paper style across all plots."""
    plt.rcParams.update({
        "figure.figsize": (8, 5),
        "figure.dpi": 120,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "grid.linestyle": "--",
    })


def ensure_output_dir() -> Path:
    PLOTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return PLOTS_OUTPUT_DIR