"""
Orkestrator scenarija.

Čita YAML definiciju scenarija, izvršava njegove korake (redom ili
paralelno) i pravi jedan ground-truth JSON fajl koji obuhvata ceo run.
Izlaz podprocesa se prikazuje uživo, pa operater vidi napredak napada
u realnom vremenu.

Upotreba:
    python run_scenario.py scenarios/basic_brute_force.yaml
    python run_scenario.py scenarios/distributed_brute_force.yaml --reset-db

Orkestrator:
    1. Opciono resetuje Postgres events + incidents tabele.
    2. Zabeleži vreme početka run-a (sidro za računanje metrika).
    3. Prođe kroz svaki korak iz YAML-a.
       - "attack"   -> pokreni Python skriptu iz attacks/.
       - "wait"     -> spavaj N sekundi (da SIEM stigne da obradi).
       - "parallel" -> pokreni više dece istovremeno.
    4. Zabeleži vreme kraja run-a.
    5. Upiše jedan objedinjen ground-truth JSON u runs/.
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
    Pun reset pipeline-a za čiste eksperimentalne run-ove:
        1. TRUNCATE Postgres events + incidents
        2. DELETE svih events-* indeksa u Elasticsearch-u
        3. FLUSHDB Redis (briše stream-ove + consumer grupe)
        4. Restart normalizer + correlator da se ponovo naprave consumer
           grupe i očisti in-memory stanje kliznih prozora.

    Fail-fast - bilo koja greška podprocesa prekida run.
    """
    print("[orchestrator] Full reset: postgres + elasticsearch + redis + services...")
    repo_root = EXPERIMENTS_DIR.parent
    user = os.environ.get("POSTGRES_USER", "siem_admin")
    db = os.environ.get("POSTGRES_DB", "siem")

    def _run(cmd: list[str], desc: str) -> None:
        result = subprocess.run(
            cmd, cwd=repo_root, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"[orchestrator] FATAL: {desc} failed")
            print(result.stderr)
            sys.exit(1)

    # 1. Postgres
    _run(
        [
            "docker", "compose", "exec", "-T", "postgres",
            "psql", "-U", user, "-d", db,
            "-c", "TRUNCATE incidents, events RESTART IDENTITY CASCADE;",
        ],
        "postgres truncate",
    )

    # 2. Elasticsearch - izlistaj pa briši jedan po jedan (wildcard je blokiran)
    list_result = subprocess.run(
        ["curl", "-s", "http://localhost:9200/_cat/indices/events-*?h=index"],
        capture_output=True, text=True,
    )
    for idx in list_result.stdout.split():
        idx = idx.strip()
        if idx:
            _run(
                ["curl", "-s", "-X", "DELETE", f"http://localhost:9200/{idx}"],
                f"es delete {idx}",
            )

    # 3. Redis
    _run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "FLUSHDB"],
        "redis flushdb",
    )

    # 4. Restartuj servise koji drže stanje ili registracije consumer grupa
    _run(
        ["docker", "compose", "restart", "normalizer", "correlator"],
        "service restart",
    )

    # Daj servisima vremena da se povežu i ponovo naprave consumer grupe
    time.sleep(3)
    print("[orchestrator] Reset complete.")

# ============================================
# Step execution
# ============================================

def run_attack_step(step: dict) -> int:
    """Pokreni attack skriptu kao podproces; prikazuj izlaz uživo."""
    script = step["script"]
    script_path = ATTACKS_DIR / script
    if not script_path.exists():
        raise FileNotFoundError(f"Attack script not found: {script_path}")

    args = [str(a) for a in step.get("args", [])]
    # Uvek reci deci-skriptama da preskoče svoj pojedinačni ground-truth
    # zapis - orkestrator pravi jedan objedinjen zapis za ceo scenario.
    args = args + ["--no-record"]

    cmd = [sys.executable, str(script_path), *args]
    print(f"[orchestrator] >> {script} {' '.join(args)}")

    proc = subprocess.run(cmd)
    return proc.returncode


def run_parallel_step(step: dict) -> List[int]:
    """Pokreni decu istovremeno, vrati listu exit kodova."""
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
    """Izvrši jedan korak. Vraća 0 na uspeh."""
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
    Upiši objedinjen ground-truth zapis za ceo run. Pojedinačne attack
    skripte orkestrator poziva sa --no-record, pa je ovaj zapis jedini.

    Funkcija usput pita API Gateway za incidente otkrivene unutar prozora
    run-a i ubaci ih kao `actual_incidents`. Time se rezultat "zaključa"
    pre nego što sledeći --reset-db obriše bazu. Koristi ga compute_metrics.py
    za računanje Precision/Recall/F1.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id()

    # Daj Alert Manageru vremena da upiše incidente u Postgres pre upita.
    print("[orchestrator] Waiting 3.0s for incidents to settle...")
    time.sleep(3.0)

    actual_incidents = _fetch_incidents_for_window(started_at, ended_at)

    record = {
        "run_id": run_id,
        "scenario": scenario.get("name", scenario_path.stem),
        "scenario_file": str(scenario_path.relative_to(EXPERIMENTS_DIR)),
        "started_at": started_at,
        "ended_at": ended_at,
        "target_base_url": target_base_url,
        "expected": scenario.get("expected_incidents", []),
        "actual_incidents": actual_incidents,
        "description": (scenario.get("description") or "").strip(),
    }
    out = RUNS_DIR / f"{run_id}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out


def _fetch_incidents_for_window(started_at: str, ended_at: str) -> list[dict]:
    """Pitaj API Gateway za incidente otkrivene unutar [started_at, ended_at]
    sa tolerancijom od 5s na svaku stranu. Vraća [] na grešku (uz log)."""
    import urllib.request
    import urllib.parse
    import urllib.error
    from datetime import timedelta

    api_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8005")
    tolerance = timedelta(seconds=5)

    def _parse(s: str):
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)

    after = (_parse(started_at) - tolerance).isoformat()
    before = (_parse(ended_at) + tolerance).isoformat()
    qs = urllib.parse.urlencode({
        "detected_after": after,
        "detected_before": before,
        "page_size": 500,
    })
    url = f"{api_url}/incidents?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("items", [])
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"[orchestrator] WARNING: incident fetch failed: {exc}")
        return []


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