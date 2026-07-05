"""
Generator normalnog saobraćaja.

Simulira legitimne korisnike koji koriste demo webapp. SIEM NE sme da
proizvede nijedan incident iz ovog saobraćaja - to je cela poenta
kontrolne grupe.

Svaki "korisnik" odradi malu sesiju:
    - 1 login pokušaj (uglavnom uspešan, ponekad jedan pogrešan pa
      uspešan drugi pokušaj)
    - Par pregleda stranica
    - Ponekad greška u URL-u (jedan 404, ne skeniranje)

Upotreba:
    python traffic_normal.py --duration 60
    python traffic_normal.py --users 10 --duration 30
    python traffic_normal.py --duration 0 --requests 50  (fiksan broj)
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from typing import List, Tuple

from base import (
    GroundTruthRecorder,
    HttpClient,
    add_common_args,
    setup_logging,
    sleep_with_jitter,
)


# Pravi kredencijali ubačeni u demo webapp. Stoje ovde da skripta može
# da radi prave uspešne login-e, a ne gomilu 401.
KNOWN_ACCOUNTS: List[Tuple[str, str]] = [
    ("alice", "Wonderland2024!"),
    ("bob",   "BuilderBob#42"),
    ("carol", "CarolPass!2024"),
]

# Javne putanje koje bi normalan korisnik pregledao.
PUBLIC_PATHS = ["/", "/health"]

# Povremene realne greške u kucanju. Nijedna sama ne bi smela da okine
# directory_scanning.
RARE_TYPOS = ["/abouts", "/contac", "/profil"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Normal-traffic generator")
    add_common_args(p)
    p.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help=(
            "How long to run, in seconds. Use 0 to run a fixed number "
            "of requests via --requests instead. (default: 60)"
        ),
    )
    p.add_argument(
        "--requests",
        type=int,
        default=0,
        help="Stop after this many requests instead of running for --duration",
    )
    p.add_argument(
        "--users",
        type=int,
        default=3,
        help="How many distinct accounts to draw from (default: 3)",
    )
    p.add_argument(
        "--typo-rate",
        type=float,
        default=0.05,
        help="Fraction of requests that hit a typo URL (default: 5%%)",
    )
    p.add_argument(
        "--wrong-pwd-rate",
        default=0.03,
        help=(
            "Fraction of login attempts where the user fat-fingers "
            "the password once and retries (default: 3%%). Real users "
            "rarely typo; 10%% across a NAT'd IP rapidly accumulates "
            "into the brute_force threshold."
        ),
    )
    p.add_argument(
        "--spoof-ip-base",
        default=None,
        help=(
            "If set, each user gets their own X-Forwarded-For IP from "
            "this base + index (e.g. base=10.0.1.0 -> 10.0.1.1, .2, .3). "
            "Requires --target-url to point directly at the webapp "
            "(port 9000) so Nginx does not rewrite the header. "
            "Used by the only_normal_traffic scenario to avoid the "
            "shared-IP NAT artifact."
        ),
    )
    return p.parse_args()


def pick_account(args) -> Tuple[str, str]:
    pool = KNOWN_ACCOUNTS[: max(1, args.users)]
    return random.choice(pool)


def do_login(
    client_factory,
    args,
    recorder,
    log,
) -> None:
    """Jedan login pokušaj. Sa verovatnoćom `wrong_pwd_rate`, korisnik
    jednom pogreši lozinku pre nego što je tačno unese."""
    username, password = pick_account(args)
    client = client_factory(username)

    if random.random() < args.wrong_pwd_rate:
        try:
            response = client.post("/login", data={
                "username": username, "password": password + "x",
            })
            log.debug("typo login %s -> %d", username, response.status_code)
            if recorder:
                recorder.action(
                    "typo_login",
                    target=username,
                    status_code=response.status_code,
                )
        except Exception as exc:
            log.warning("typo login failed: %s", exc)
        sleep_with_jitter(args.delay * 0.5)

    try:
        response = client.post("/login", data={
            "username": username, "password": password,
        })
        log.debug("login %s -> %d", username, response.status_code)
        if recorder:
            recorder.action(
                "successful_login" if response.status_code == 200 else "failed_login",
                target=username,
                status_code=response.status_code,
            )
    except Exception as exc:
        log.warning("login failed: %s", exc)


def do_browse(
    client_factory,
    args,
    recorder,
    log,
) -> None:
    """Jedan GET zahtev..."""
    if random.random() < args.typo_rate:
        path = random.choice(RARE_TYPOS)
    else:
        path = random.choice(PUBLIC_PATHS)
    # Pregled koristi "deljeni" klijent (prvi korisnik kao sidro) jer
    # simuliramo preglede stranica, ne autentifikaciju.
    client = client_factory(KNOWN_ACCOUNTS[0][0])

    try:
        response = client.get(path)
        log.debug("GET %s -> %d", path, response.status_code)
        if recorder:
            recorder.action(
                "page_view",
                target=path,
                status_code=response.status_code,
            )
    except Exception as exc:
        log.warning("GET %s failed: %s", path, exc)


def ip_for_user(base: str, username: str, pool: List[Tuple[str, str]]) -> str:
    """
    Mapira username na determinističku IP iz `base + index`.

    Primer: base="10.0.1.0", users=[alice, bob, carol]
        alice -> 10.0.1.1
        bob   -> 10.0.1.2
        carol -> 10.0.1.3
    """
    base_octets = base.split(".")
    if len(base_octets) != 4:
        raise ValueError(f"Invalid base IP: {base}")
    base_last = int(base_octets[3])
    usernames = [u for u, _ in pool]
    try:
        idx = usernames.index(username)
    except ValueError:
        idx = abs(hash(username)) % 250  # rezerva za nepoznate korisnike
    return f"{base_octets[0]}.{base_octets[1]}.{base_octets[2]}.{base_last + 1 + idx}"


def main() -> int:
    args = parse_args()
    log = setup_logging("normal-traffic", level=args.log_level)

    if args.duration == 0 and args.requests == 0:
        log.error("Must specify either --duration or --requests > 0")
        return 1

    log.info(
        "Starting normal traffic: duration=%.0fs requests=%d users=%d delay=%.2fs",
        args.duration, args.requests, args.users, args.delay,
    )

    # Fabrika klijenata po korisniku. Sa --spoof-ip-base, svaki korisnik
    # dobije svoj X-Forwarded-For header, da se svi ne stope u jednu IP
    # i slučajno okinu pravila praga (NAT artefakt koji bi produkcija
    # rešila slojevitom detekcijom ili UEBA - videti Poglavlje 7, budući rad).
    if args.spoof_ip_base:
        clients: dict = {}
        def client_factory(username: str) -> HttpClient:
            if username not in clients:
                ip = ip_for_user(args.spoof_ip_base, username, KNOWN_ACCOUNTS)
                clients[username] = HttpClient(
                    args.target_url, logger=log, spoof_ip=ip,
                )
            return clients[username]
    else:
        shared = HttpClient(args.target_url, logger=log)
        def client_factory(username: str) -> HttpClient:
            return shared

    recorder = None
    if not args.no_record:
        recorder = GroundTruthRecorder(
            scenario="traffic_normal",
            target_base_url=args.target_url,
            notes=(
                f"duration={args.duration} requests={args.requests} "
                f"users={args.users}"
            ),
        )
        # Bez očekivanja: ovaj scenario treba da proizvede NULA incidenata.
        # Incident ovde bi u metrikama bio False Positive.

    started = time.time()
    sent = 0

    while True:
        # Stop conditions
        if args.requests > 0 and sent >= args.requests:
            break
        if args.duration > 0 and (time.time() - started) >= args.duration:
            break

        # 70% pregled, 30% login - tipičan miks saobraćaja veb aplikacije
        if random.random() < 0.3:
            do_login(client_factory, args, recorder, log)
        else:
            do_browse(client_factory, args, recorder, log)

        sent += 1
        sleep_with_jitter(args.delay)

    elapsed = time.time() - started
    log.info("Done: %d requests over %.1fs", sent, elapsed)

    if recorder:
        path_out = recorder.close()
        log.info("Ground truth written to %s", path_out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
