"""
Zajednički delovi za skripte simulacije napada.

Nudi:
    - HttpClient: tanak omotač oko requests sa razumnim podrazumevanim vrednostima
    - GroundTruthRecorder: append-only dnevnik događaja upisan kao JSON na disk
    - Argparse pomoćnike zajedničke svim skriptama

Svaka attack skripta ovo uvozi umesto da duplira isti kod.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import requests


# ============================================
# Logging setup (consistent across scripts)
# ============================================

def setup_logging(name: str, level: str = "INFO") -> logging.Logger:
    """Formatiraj log kao jednu liniju, da se konzolni izlaz lako grep-uje."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


# ============================================
# HTTP client
# ============================================

class HttpClient:
    """
    Omotava requests.Session sa baznim URL-om i tolerancijom na greške.

    Attack skripte NE smeju da padnu na jedan 4xx - to je često i poenta
    napada. Ovaj klijent zabeleži odgovor i vrati ga.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        user_agent: str = "siem-attack-sim/1.0",
        logger: Optional[logging.Logger] = None,
        spoof_ip: Optional[str] = None,
    ):
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        # Simulacija distribuiranog napada: kad gađamo webapp direktno
        # (zaobilazeći Nginx), X-Forwarded-For header je jedini signal
        # koji webapp koristi da odredi source IP.
        if spoof_ip:
            self._session.headers["X-Forwarded-For"] = spoof_ip
        self._log = logger or logging.getLogger(__name__)

    def get(self, path: str) -> requests.Response:
        url = self._build(path)
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            self._log.warning("GET %s failed: %s", path, exc)
            raise
        self._log.debug("GET %s -> %d", path, response.status_code)
        return response

    def post(
        self,
        path: str,
        *,
        data: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> requests.Response:
        url = self._build(path)
        try:
            response = self._session.post(
                url,
                data=data,
                json=json_body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self._log.warning("POST %s failed: %s", path, exc)
            raise
        self._log.debug("POST %s -> %d", path, response.status_code)
        return response

    def _build(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self._base + path


# ============================================
# Ground truth
# ============================================

@dataclass
class GroundTruthAction:
    """Jedna stvar koju je attack skripta uradila, sa vremenom."""
    timestamp: str
    action: str         # "failed_login", "404_probe", "successful_login", ...
    target: Optional[str] = None
    status_code: Optional[int] = None
    extra: dict = field(default_factory=dict)


@dataclass
class GroundTruthExpectation:
    """Šta napad očekuje da SIEM detektuje."""
    rule: str           # "brute_force", "directory_scanning", "account_takeover"
    severity: str
    min_count: int = 1  # Minimalan broj incidenata koji odgovaraju ovom pravilu


@dataclass
class GroundTruthRun:
    """
    Čuva se kao JSON u experiments/runs/<run_id>.json.

    Nizvodno ga koristi compute_metrics.py za računanje Precision,
    Recall i F1.
    """
    run_id: str
    scenario: str
    started_at: str
    ended_at: Optional[str] = None
    target_base_url: str = ""
    expected: List[GroundTruthExpectation] = field(default_factory=list)
    actions: List[GroundTruthAction] = field(default_factory=list)
    notes: str = ""


class GroundTruthRecorder:
    """
    Append-only recorder. Upisuje na disk pri close().

    Upotreba:
        recorder = GroundTruthRecorder(scenario="brute_force_basic", ...)
        recorder.expect(rule="brute_force", severity="high")
        recorder.action("failed_login", target="alice", status_code=401)
        recorder.close()  # upiše runs/<id>.json
    """

    def __init__(
        self,
        *,
        scenario: str,
        target_base_url: str,
        runs_dir: Optional[Path] = None,
        notes: str = "",
    ):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self._run = GroundTruthRun(
            run_id=run_id,
            scenario=scenario,
            started_at=_iso_now(),
            target_base_url=target_base_url,
            notes=notes,
        )
        self._runs_dir = runs_dir or _default_runs_dir()
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def run_id(self) -> str:
        return self._run.run_id

    def expect(self, *, rule: str, severity: str, min_count: int = 1) -> None:
        self._run.expected.append(GroundTruthExpectation(
            rule=rule, severity=severity, min_count=min_count,
        ))

    def action(
        self,
        action: str,
        *,
        target: Optional[str] = None,
        status_code: Optional[int] = None,
        **extra: Any,
    ) -> None:
        self._run.actions.append(GroundTruthAction(
            timestamp=_iso_now(),
            action=action,
            target=target,
            status_code=status_code,
            extra=extra,
        ))

    def close(self) -> Path:
        self._run.ended_at = _iso_now()
        path = self._runs_dir / f"{self._run.run_id}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(self._run), f, indent=2, ensure_ascii=False)
        return path


# ============================================
# Argparse helpers
# ============================================

def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Flagovi koje prihvata svaka attack skripta."""
    parser.add_argument(
        "--target-url",
        default=os.environ.get("ATTACK_TARGET_URL", "http://localhost:8080"),
        help="Base URL of the demo webapp (default: localhost:8080)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between requests (default: 0.5)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="Skip writing the ground-truth JSON (useful for dry runs)",
    )


# ============================================
# Internals
# ============================================

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_runs_dir() -> Path:
    """Nađi experiments/runs u odnosu na experiments/ folder."""
    return Path(__file__).resolve().parent.parent / "runs"


def sleep_with_jitter(base: float, jitter_factor: float = 0.2) -> None:
    """Spavaj `base` sekundi +/- jitter, da tajming ne bude robotski."""
    import random
    jitter = base * jitter_factor
    actual = max(0, base + random.uniform(-jitter, jitter))
    time.sleep(actual)