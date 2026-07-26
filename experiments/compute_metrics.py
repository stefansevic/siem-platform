"""
Računa metrike detekcije za SIEM run-ove, po instanci napada.

Čita ground-truth JSON fajlove iz experiments/runs/. Svaki fajl sadrži
`expected` (lista očekivanih incidenata, svaki {rule, [user]}) i
`actual_incidents` (detektovani incidenti iz API-ja).

Metod (ispravke po recenziji, nalazi C1-C5):
    * Uparivanje PO INSTANCI: svaki očekivani incident se pokušava upariti
      sa jednim detektovanim po (rule, user). Upareni -> TP; neupareni
      očekivani -> FN; neupareni detektovani -> FP. Time više alarma istog
      pravila ne uvećava TP, a alarm za pogrešnog korisnika je FP.
    * KONTROLNI scenariji (expected prazan, nema napada) NEMAJU recall/F1;
      za njih se meri broj lažnih alarma (FP). Ranija greška je bila da su
      dobijali Recall = 1,0 i F1 = 1,0.
    * Latencija se meri kao vreme od prvog događaja napada do detekcije;
      to je detekciono vreme (uključuje tempo napada), ne čista latencija
      platforme. Ne koristi se za tvrdnje o brzini platforme.

Izlazi:
    experiments/results/per_run.jsonl     - jedna linija po run-u
    experiments/results/per_rule.csv      - TP/FP/FN + P/R/F1 po pravilu
    experiments/results/per_scenario.csv  - po scenariju (napad: P/R/F1;
                                            kontrola: broj lažnih alarma)

Upotreba:
    python compute_metrics.py
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


DEFAULT_RUNS_DIR = "experiments/runs"
DEFAULT_OUTPUT_DIR = "experiments/results"


def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def load_runs(runs_dir: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            with path.open() as fh:
                runs.append(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warn: skipping {path.name}: {exc}", file=sys.stderr)
    return runs


def _match(expected: list[dict], detected: list[dict]):
    """Upari očekivane sa detektovanim po (rule, user).

    Vraća (tp_parovi, fn_lista, fp_lista).
    """
    remaining = list(detected)
    tp_pairs: list[tuple[dict, dict]] = []
    fn_list: list[dict] = []
    for exp in expected:
        rule = exp["rule"]
        user = exp.get("user")
        found = None
        for d in remaining:
            if d.get("rule_name") != rule:
                continue
            if user is not None and (d.get("target_user_name") or None) != user:
                continue
            found = d
            break
        if found is not None:
            remaining.remove(found)
            tp_pairs.append((exp, found))
        else:
            fn_list.append(exp)
    return tp_pairs, fn_list, remaining  # remaining = FP


def _latency_seconds(incident: dict) -> Optional[float]:
    det = _parse_ts(incident.get("detected_at", ""))
    first = _parse_ts(incident.get("first_event_at", ""))
    if det is None or first is None:
        return None
    return (det - first).total_seconds()


def compute_run_metrics(run: dict[str, Any]) -> dict[str, Any]:
    expected = run.get("expected", []) or []
    detected = run.get("actual_incidents", []) or []
    is_control = len(expected) == 0

    tp_pairs, fn_list, fp_list = _match(expected, detected)
    tp, fn, fp = len(tp_pairs), len(fn_list), len(fp_list)

    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1: Optional[float] = 2 * precision * recall / (precision + recall)
    else:
        f1 = None

    latencies = [
        lat for lat in (_latency_seconds(d) for _, d in tp_pairs)
        if lat is not None
    ]

    return {
        "run_id": run.get("run_id"),
        "scenario": run.get("scenario"),
        "kind": "control" if is_control else "attack",
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        # po pravilu (za agregaciju): rule -> {tp, fn, fp}
        "per_rule": _per_rule_counts(tp_pairs, fn_list, fp_list),
        "latencies": latencies,
    }


def _per_rule_counts(tp_pairs, fn_list, fp_list) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fn": 0, "fp": 0}
    )
    for exp, _ in tp_pairs:
        counts[exp["rule"]]["tp"] += 1
    for exp in fn_list:
        counts[exp["rule"]]["fn"] += 1
    for inc in fp_list:
        rule = inc.get("rule_name", "unknown")
        counts[rule]["fp"] += 1
    return {k: dict(v) for k, v in counts.items()}


def aggregate_per_rule(runs: list[dict]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    for run in runs:
        for rule, c in run["per_rule"].items():
            totals[rule]["tp"] += c.get("tp", 0)
            totals[rule]["fp"] += c.get("fp", 0)
            totals[rule]["fn"] += c.get("fn", 0)

    rows = []
    for rule, t in sorted(totals.items()):
        tp, fp, fn = t["tp"], t["fp"], t["fn"]
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        rows.append({
            "rule_name": rule, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
        })
    return rows


def aggregate_per_scenario(runs: list[dict]) -> list[dict[str, Any]]:
    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        by_scenario[run["scenario"]].append(run)

    rows = []
    for scenario, group in sorted(by_scenario.items()):
        n = len(group)
        kind = group[0]["kind"]
        row: dict[str, Any] = {"scenario": scenario, "kind": kind, "n_runs": n}
        if kind == "control":
            fps = [g["fp"] for g in group]
            clean = sum(1 for g in group if g["fp"] == 0)
            row.update({
                "mean_false_positives": round(statistics.mean(fps), 4),
                "runs_without_fp": clean,
                "fp_rate": round(1 - clean / n, 4),  # udeo run-ova sa lažnim alarmom
                "precision_mean": "", "recall_mean": "", "f1_mean": "",
            })
        else:
            def m(key):
                vals = [g[key] for g in group if g[key] is not None]
                return round(statistics.mean(vals), 4) if vals else 0.0
            def s(key):
                vals = [g[key] for g in group if g[key] is not None]
                return round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0
            row.update({
                "precision_mean": m("precision"), "precision_std": s("precision"),
                "recall_mean": m("recall"), "recall_std": s("recall"),
                "f1_mean": m("f1"), "f1_std": s("f1"),
                "mean_false_positives": "", "runs_without_fp": "", "fp_rate": "",
            })
        rows.append(row)
    return rows


def write_jsonl(path: Path, runs: list[dict]) -> None:
    with path.open("w") as fh:
        for run in runs:
            fh.write(json.dumps(run) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("")
        return
    # ujednači kolone preko svih redova
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


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

    write_jsonl(args.output_dir / "per_run.jsonl", metrics)
    write_csv(args.output_dir / "per_rule.csv", aggregate_per_rule(metrics))
    write_csv(args.output_dir / "per_scenario.csv", aggregate_per_scenario(metrics))

    # kratak sažetak latencije detektovanih napada
    all_lat = [lat for m in metrics for lat in m["latencies"]]
    if all_lat:
        print(f"Detection latency (s): median={statistics.median(all_lat):.2f} "
              f"min={min(all_lat):.2f} max={max(all_lat):.2f} n={len(all_lat)}")
    print(f"Processed {len(metrics)} runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
