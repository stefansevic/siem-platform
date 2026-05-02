"""
Brute-force attack simulator.

Sends repeated POST /login requests with wrong passwords, optionally
followed by the correct one. The SIEM should fire a `brute_force` rule
once the per-IP failure threshold is crossed; if the correct password
is revealed at the end and the failure count is high enough, an
`account_takeover` should also fire.

Usage:
    python brute_force.py --username alice --attempts 10
    python brute_force.py --username admin --attempts 20 --delay 0.2
    python brute_force.py --username alice --attempts 6 \\
        --reveal-password 'Wonderland2024!'
"""

from __future__ import annotations

import argparse
import sys

from base import (
    GroundTruthRecorder,
    HttpClient,
    add_common_args,
    setup_logging,
    sleep_with_jitter,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Brute-force login simulator")
    add_common_args(p)
    p.add_argument(
        "--username",
        required=True,
        help="Account to attack (e.g. alice, admin)",
    )
    p.add_argument(
        "--attempts",
        type=int,
        default=10,
        help="Number of failed login attempts (default: 10)",
    )
    p.add_argument(
        "--password-prefix",
        default="wrong",
        help='Wrong-password stem; final password is "<prefix>_<i>" (default: wrong)',
    )
    p.add_argument(
        "--reveal-password",
        default=None,
        help=(
            "If set, sends one final correct login attempt with this "
            "password. Useful for triggering account_takeover."
        ),
    )
    p.add_argument(
        "--spoof-ip",
        default=None,
        help=(
            "Override the source IP via the X-Forwarded-For header. "
            "Required for the distributed_brute_force scenario where "
            "multiple attacker IPs are simulated. The target URL should "
            "point directly to the webapp (port 9000) so Nginx does not "
            "rewrite the header."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log = setup_logging("brute-force", level=args.log_level)

    log.info(
        "Starting brute-force attack: username=%s attempts=%d delay=%.2fs",
        args.username, args.attempts, args.delay,
    )

    client = HttpClient(
        args.target_url,
        logger=log,
        spoof_ip=args.spoof_ip,
    )

    recorder = None
    if not args.no_record:
        recorder = GroundTruthRecorder(
            scenario="brute_force",
            target_base_url=args.target_url,
            notes=(
                f"username={args.username} attempts={args.attempts} "
                f"reveal={'yes' if args.reveal_password else 'no'}"
            ),
        )
        recorder.expect(rule="brute_force", severity="high")
        if args.reveal_password and args.attempts >= 5:
            recorder.expect(rule="account_takeover", severity="critical")

    # ----- Phase 1: wrong passwords -----
    failures = 0
    for i in range(1, args.attempts + 1):
        password = f"{args.password_prefix}_{i}"
        try:
            response = client.post(
                "/login",
                data={"username": args.username, "password": password},
            )
        except Exception as exc:
            log.error("Attempt %d failed: %s", i, exc)
            if recorder:
                recorder.action(
                    "failed_login",
                    target=args.username,
                    status_code=None,
                    error=str(exc),
                )
            sleep_with_jitter(args.delay)
            continue

        log.info("Attempt %d: %d", i, response.status_code)
        if recorder:
            recorder.action(
                "failed_login",
                target=args.username,
                status_code=response.status_code,
            )
        if response.status_code == 401:
            failures += 1

        sleep_with_jitter(args.delay)

    # ----- Phase 2: optional correct password reveal -----
    if args.reveal_password:
        log.info("Revealing correct password...")
        try:
            response = client.post(
                "/login",
                data={
                    "username": args.username,
                    "password": args.reveal_password,
                },
            )
            log.info("Reveal: %d", response.status_code)
            if recorder:
                recorder.action(
                    "successful_login" if response.status_code == 200 else "failed_login",
                    target=args.username,
                    status_code=response.status_code,
                )
        except Exception as exc:
            log.error("Reveal request failed: %s", exc)

    # ----- Wrap up -----
    log.info(
        "Done: %d/%d failed-login responses observed",
        failures, args.attempts,
    )

    if recorder:
        path = recorder.close()
        log.info("Ground truth written to %s", path)

    return 0


if __name__ == "__main__":
    sys.exit(main())