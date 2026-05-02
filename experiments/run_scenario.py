"""
Scenario orchestrator.

Reads a YAML scenario definition, executes its steps (sequentially
or in parallel), and produces a single ground-truth JSON file that
captures the whole run. Subprocess output is streamed to the console
so the operator can see attack progress in real time.

Usage:
    python run_scenario.py scenarios/basic_brute_force.yaml
    python run_scenario.py scenarios/distributed_brute_force.yaml --reset-db

The orchestrator:
    1. Optionally resets Postgres events + incidents tables.
    2. Records the run start time (anchor for metric computation).
    3. Walks every step in the YAML.
       - "attack" -> run a Python script in attacks/.
       - "wait"   -> sleep N seconds (lets the SIEM finish processing).
       - "parallel" -> run multiple children concurrently.
    4. Records the run end time.
    5. Writes a single combined ground-truth JSON to runs/.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml


# ============================================
# Paths
# ============================================

EXPERIMENTS_DIR = Path(__file__).resolve().parent
ATTACKS_DIR = EXPERIMENTS_DIR / "attacks"
RUNS_DIR = EXPERIMENTS_DIR / "runs"


# ============================================
# CLI
# ============================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a scenario YAML")
    p.add_argument("scenario", help="Path to scenario YAML")
    p.add_argument(
        "--reset-db",
        action="store_true",
        help="Wipe events + incidents tables before running",
    )
    p.add_argument(
        "--no-record",
        action="store_true",
        help="Skip writing the consolidated ground-truth JSON",
    )
    return p.parse_args()


# ============================================
# Postgres reset
# ============================================

def reset_database() -> None:
    """
    Wipe events + incidents via docker compose exec.
    Uses the env variables already configured in .env.
    """
    print("[orchestrator] Resetting Postgres events + incidents...")
    user = os.environ.get("POSTGRES_USER", "siem")
    db = os.environ.get("POSTGRES_DB", "siem")
    cmd = [
        "docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", user, "-d", db,
        "-c", "DELETE FROM events; DELETE FROM incidents;",
    ]
    result = subprocess.run(
        cmd,
        cwd=EXPERIMENTS_DIR.parent,  # repo root, where docker-compose.yml lives
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[orchestrator] WARNING: db reset failed:")
        print(result.stderr)


# ============================================
# Step execution
# ============================================

def run_attack_step(step: dict) -> int:
    """Run an attack script as a subprocess; stream output live."""
    script = step["script"]
    script_path = ATTACKS_DIR / script
    if not script_path.exists():
        raise FileNotFoundError(f"Attack script not found: {script_path}")

    args = [str(a) for a in step.get("args", [])]
    # Always tell child scripts to skip their individual ground-truth
    # records — the orchestrator builds a single combined record
    # for the whole scenario.
    args = args + ["--no-record"]

    cmd = ["python3.11", str(script_path), *args]
    print(f"[orchestrator] >> {script} {' '.join(args)}")

    proc = subprocess.run(cmd)
    return proc.returncode


def run_parallel_step(step: dict) -> List[int]:
    """Run children concurrently, return the list of exit codes."""
    children = step.get("children", [])
    if not children:
        return []

    print(f"[orchestrator] Starting {len(children)} parallel children...")

    results: List[Optional[int]] = [None] * len(children)
    threads: List[threading.Thread] = []

    def runner(idx: int, child_step: dict) -> None:
        results[idx] = run_step(child_step)

    for i, child in enumerate(children):
        t = threading.Thread(target=runner, args=(i, child))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("[orchestrator] All parallel children complete.")
    return [r if r is not None else -1 for r in results]


def run_step(step: dict) -> int:
    """Dispatch a single step. Returns 0 on success."""
    step_type = step.get("type")
    if step_type == "attack":
        return run_attack_step(step)
    if step_type == "wait":
        seconds = float(step.get("seconds", 0))
        print(f"[orchestrator] Waiting {seconds}s...")
        time.sleep(seconds)
        return 0
    if step_type == "parallel":
        codes = run_parallel_step(step)
        return max(codes) if codes else 0
    raise ValueError(f"Unknown step type: {step_type!r}")


# ============================================
# Ground truth
# ============================================

def make_run_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        + uuid.uuid4().hex[:6]
    )


def write_ground_truth(
    *,
    scenario: dict,
    scenario_path: Path,
    started_at: str,
    ended_at: str,
    target_base_url: str,
) -> Path:
    """
    Write a consolidated ground-truth record covering the whole run.

    Individual attack scripts are invoked with --no-record by the
    orchestrator, so the only record produced is this one. Used by
    compute_metrics.py (Week 11) to evaluate Precision / Recall / F1.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id()

    record = {
        "run_id": run_id,
        "scenario": scenario.get("name", scenario_path.stem),
        "scenario_file": str(scenario_path.relative_to(EXPERIMENTS_DIR)),
        "started_at": started_at,
        "ended_at": ended_at,
        "target_base_url": target_base_url,
        "expected": scenario.get("expected_incidents", []),
        "description": (scenario.get("description") or "").strip(),
    }

    out = RUNS_DIR / f"{run_id}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out


# ============================================
# Main
# ============================================

def main() -> int:
    args = parse_args()

    scenario_path = Path(args.scenario).resolve()
    if not scenario_path.exists():
        print(f"Scenario not found: {scenario_path}", file=sys.stderr)
        return 1

    scenario = yaml.safe_load(scenario_path.read_text())
    name = scenario.get("name", scenario_path.stem)

    print(f"[orchestrator] === Scenario: {name} ===")

    if args.reset_db:
        reset_database()

    started_at = datetime.now(timezone.utc).isoformat()

    rc = 0
    for step in scenario.get("steps", []):
        rc = run_step(step)
        if rc != 0:
            print(f"[orchestrator] Step exited with code {rc}, continuing...")

    ended_at = datetime.now(timezone.utc).isoformat()

    if not args.no_record:
        out_path = write_ground_truth(
            scenario=scenario,
            scenario_path=scenario_path,
            started_at=started_at,
            ended_at=ended_at,
            target_base_url=os.environ.get(
                "ATTACK_TARGET_URL", "http://localhost:8080",
            ),
        )
        print(f"[orchestrator] Ground truth: {out_path}")

    print(f"[orchestrator] === Done: {name} ===")
    return rc


if __name__ == "__main__":
    sys.exit(main())