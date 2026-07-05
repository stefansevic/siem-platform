"""
Simulator napada skeniranja direktorijuma.

Probava listu čestih putanja koje verovatno vraćaju 404. SIEM treba da
okine `directory_scanning` pravilo kad vidi dovoljno različitih 404
odgovora sa iste IP unutar prozora pravila.

Podrazumevana wordlista je mala (~60 putanja) i ugrađena, pa skripta
radi bez eksternih fajlova; prosledi --wordlist <path> za SecLists ili
sličan. Wordlista se filtrira od praznih linija i komentara.

Upotreba:
    python directory_scan.py
    python directory_scan.py --paths-count 30 --delay 0.05
    python directory_scan.py --wordlist common.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from base import (
    GroundTruthRecorder,
    HttpClient,
    add_common_args,
    setup_logging,
    sleep_with_jitter,
)


# Razumna početna wordlista. Svaki unos je putanja koju napadači često
# probaju tražeći pogrešne konfiguracije ili procurele tajne.
DEFAULT_WORDLIST = [
    "admin", "administrator", "wp-admin", "phpmyadmin", "phpinfo.php",
    "backup", "backups", "bak", "old", "tmp", "temp", "test",
    "config", "config.php", "configuration", "settings", "settings.json",
    ".env", ".env.local", ".env.production",
    ".git", ".git/config", ".git/HEAD",
    ".svn", ".DS_Store",
    "robots.txt", "sitemap.xml", "humans.txt", "crossdomain.xml",
    "api/v1", "api/v2", "api/users", "api/admin",
    "users", "user", "users.json", "users.csv",
    "login.php", "wp-login.php", "signin", "register.php",
    "uploads", "files", "assets/private", "data",
    "secret", "secrets", "private", "hidden",
    "database", "db", "db.sql", "dump.sql", "backup.sql",
    "logs", "log", "var/log", "errors.log",
    "console", "shell", "cmd",
    "phpunit", "vendor", "node_modules", "composer.json",
    "package.json", "package-lock.json",
    "src", "lib", "bin", "etc/passwd", "var/www",
    "cgi-bin", "admin.php", "manage", "manager",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Directory-scanning simulator")
    add_common_args(p)
    p.add_argument(
        "--wordlist",
        default=None,
        help="Path to a newline-separated wordlist (default: built-in list)",
    )
    p.add_argument(
        "--paths-count",
        type=int,
        default=30,
        help="How many distinct paths to probe (default: 30)",
    )
    p.add_argument(
        "--delay-min",
        type=float,
        default=None,
        help="Override the delay (overrides --delay for fast scans)",
    )
    return p.parse_args()


def load_wordlist(path: str | None) -> List[str]:
    if not path:
        return list(DEFAULT_WORDLIST)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Wordlist not found: {path}")
    lines = [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
    ]
    return [line for line in lines if line and not line.startswith("#")]


def main() -> int:
    args = parse_args()
    log = setup_logging("dir-scan", level=args.log_level)

    delay = args.delay_min if args.delay_min is not None else args.delay
    log.info(
        "Starting directory scan: paths=%d delay=%.2fs",
        args.paths_count, delay,
    )

    wordlist = load_wordlist(args.wordlist)
    if len(wordlist) < args.paths_count:
        log.warning(
            "Wordlist has %d entries, fewer than requested %d paths; "
            "using all of them.",
            len(wordlist), args.paths_count,
        )
    paths = wordlist[: args.paths_count]

    client = HttpClient(args.target_url, logger=log)

    recorder = None
    if not args.no_record:
        recorder = GroundTruthRecorder(
            scenario="directory_scanning",
            target_base_url=args.target_url,
            notes=f"paths={len(paths)} delay={delay}",
        )
        recorder.expect(rule="directory_scanning", severity="medium")

    not_found = 0
    for i, path in enumerate(paths, start=1):
        try:
            response = client.get(f"/{path}")
        except Exception as exc:
            log.error("GET /%s failed: %s", path, exc)
            sleep_with_jitter(delay)
            continue

        log.info("GET /%s -> %d", path, response.status_code)
        if recorder:
            recorder.action(
                "path_probe",
                target=f"/{path}",
                status_code=response.status_code,
            )
        if response.status_code == 404:
            not_found += 1

        sleep_with_jitter(delay)

    log.info("Done: %d/%d responses were 404", not_found, len(paths))

    if recorder:
        path_out = recorder.close()
        log.info("Ground truth written to %s", path_out)

    return 0


if __name__ == "__main__":
    sys.exit(main())