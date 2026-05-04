"""
Batch experiment orchestrator.

Reads run_all.config.yaml, executes each scenario the configured number
of times via run_scenario.py --reset-db, and logs results to both stdout
and run_all.log.

Aborts the suite if 3 consecutive runs fail (system likely down).

Usage:
    python run_all.py
    python run_all.py --config experiments/run_all.config.yaml
    python run_all.py --dry-run            # show plan, do nothing
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

EXPERIMENTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXPERIMENTS_DIR.parent
DEFAULT_CONFIG = EXPERIMENTS_DIR / "run_all.config.yaml"
LOG_FILE = EXPERIMENTS_DIR / "run_all.log"

CONSECUTIVE_FAIL_LIMIT = 3


class Logger:
    """Writes to stdout and a log file with a timestamp prefix."""

    def __init__(self, log_path: Path) -> None:
        self.fh = log_path.open("a")
        self._line(f"\n=== Suite started at {self._now()} ===")

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _line(self, msg: str) -> None:
        ts = self._now()
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def info(self, msg: str) -> None:
        self._line(msg)

    def close(self) -> None:
        self._line(f"=== Suite ended at {self._now()} ===\n")
        self.fh.close()


def load_config(path: Path) -> dict[str, int]:
    """Return {scenario_name: run_count} from the YAML config."""
    with path.open() as fh:
        data = yaml.safe_load(fh)
    runs = data.get("runs_per_scenario", {})
    if not runs:
        raise ValueError(f"config {path} has no runs_per_scenario block")
    return runs


def scenario_path(scenario_name: str) -> Path:
    """Resolve scenario YAML path; expects experiments/scenarios/<name>.yaml."""
    return EXPERIMENTS_DIR / "scenarios" / f"{scenario_name}.yaml"


def run_one(scenario_name: str, log: Logger) -> bool:
    """Execute one run via run_scenario.py --reset-db. Return True on success."""
    yaml_path = scenario_path(scenario_name)
    if not yaml_path.is_file():
        log.info(f"  ERROR: scenario file not found: {yaml_path}")
        return False

    cmd = [
        sys.executable,
        str(EXPERIMENTS_DIR / "run_scenario.py"),
        str(yaml_path),
        "--reset-db",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        log.info(f"  TIMEOUT after 300s")
        return False

    if result.returncode != 0:
        log.info(f"  FAILED (exit {result.returncode})")
        # Persist tail of stderr to log only (stdout stays clean)
        tail = (result.stderr or "").strip().splitlines()[-5:]
        for line in tail:
            log.fh.write(f"    stderr: {line}\n")
        log.fh.flush()
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and exit without executing.",
    )
    args = parser.parse_args()

    if not args.config.is_file():
        print(f"error: config not found: {args.config}", file=sys.stderr)
        return 1

    runs_per_scenario = load_config(args.config)
    total_runs = sum(runs_per_scenario.values())

    print(f"Plan: {total_runs} runs across {len(runs_per_scenario)} scenarios")
    for name, count in runs_per_scenario.items():
        print(f"  {name}: {count} runs")

    if args.dry_run:
        return 0

    log = Logger(LOG_FILE)
    log.info(f"Total planned runs: {total_runs}")

    started = time.monotonic()
    completed = 0
    failed: list[tuple[str, int]] = []   # (scenario, run_index)
    consecutive_fails = 0

    for scenario_name, run_count in runs_per_scenario.items():
        log.info(f"--- Scenario: {scenario_name} ({run_count} runs) ---")
        for i in range(1, run_count + 1):
            global_idx = completed + 1
            log.info(f"[{global_idx}/{total_runs}] {scenario_name} run {i}/{run_count}")
            ok = run_one(scenario_name, log)
            if ok:
                consecutive_fails = 0
            else:
                consecutive_fails += 1
                failed.append((scenario_name, i))
                if consecutive_fails >= CONSECUTIVE_FAIL_LIMIT:
                    log.info(
                        f"ABORT: {CONSECUTIVE_FAIL_LIMIT} consecutive failures "
                        f"— system likely down"
                    )
                    log.close()
                    return 2
            completed += 1

    elapsed = time.monotonic() - started
    ok_count = completed - len(failed)
    log.info(
        f"Done: {ok_count}/{completed} OK in {elapsed:.0f}s "
        f"({len(failed)} failed)"
    )
    if failed:
        log.info("Failed runs:")
        for name, idx in failed:
            log.info(f"  {name} #{idx}")
    log.close()
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())