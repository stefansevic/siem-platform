"""
Normal-traffic generator.

Simulates legitimate users hitting the demo webapp. The SIEM should
NOT generate any incidents from this traffic — that is the whole
point of the control group.

Each "user" performs a small session:
    - 1 login attempt (mostly successful, occasionally one wrong try
      then a successful retry)
    - A few page views
    - Sometimes a typo in a URL (one 404, not a scan)

Usage:
    python traffic_normal.py --duration 60
    python traffic_normal.py --users 10 --duration 30
    python traffic_normal.py --duration 0 --requests 50  (fixed count)
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


# Real account credentials seeded into the demo webapp. Lives here
# so the script can perform real successful logins, not flooded 401s.
KNOWN_ACCOUNTS: List[Tuple[str, str]] = [
    ("alice", "Wonderland2024!"),
    ("bob",   "BuilderBob#42"),
    ("carol", "CarolPass!2024"),
]

# Public paths a normal user might browse.
PUBLIC_PATHS = ["/", "/health"]

# Occasional realistic typos. None of these should trigger
# directory_scanning on its own.
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
    """One login attempt. With probability `wrong_pwd_rate`, the user
    fat-fingers the password once before getting it right."""
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
    """One GET request..."""
    if random.random() < args.typo_rate:
        path = random.choice(RARE_TYPOS)
    else:
        path = random.choice(PUBLIC_PATHS)
    # Browse traffic uses a "shared" client (first user as anchor) since
    # we are simulating page views, not authentication.
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
    Map a username to a deterministic IP from `base + index`.

    Example: base="10.0.1.0", users=[alice, bob, carol]
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
        idx = abs(hash(username)) % 250  # fallback for unknown users
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

    # Per-user client factory. With --spoof-ip-base, each user gets
    # their own X-Forwarded-For header so they don't all collapse into
    # one IP and accidentally trigger threshold rules (a NAT artifact
    # that real-world deployments would handle with layered detection
    # or UEBA — see Chapter 7, Future work).
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
        # No expectations: this scenario should produce ZERO incidents.
        # An incident here would be a False Positive in metrics.

    started = time.time()
    sent = 0

    while True:
        # Stop conditions
        if args.requests > 0 and sent >= args.requests:
            break
        if args.duration > 0 and (time.time() - started) >= args.duration:
            break

        # 70% browse, 30% login — typical web app traffic mix
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
