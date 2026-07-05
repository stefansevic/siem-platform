"""
Simulator preuzimanja naloga (account takeover).

Izvede N neuspelih login-a praćenih tačno jednim uspešnim, u skladu sa
semantikom `account_takeover` pravila. Zgodan kao samostalan scenario
gde treba da okinu I brute_force I account_takeover.

Upotreba:
    python account_takeover.py --username alice --password 'Wonderland2024!'
    python account_takeover.py --username admin --password admin123 --failed 6
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
    p = argparse.ArgumentParser(description="Account-takeover simulator")
    add_common_args(p)
    p.add_argument(
        "--username",
        required=True,
        help="Account to take over",
    )
    p.add_argument(
        "--password",
        required=True,
        help="The correct password to reveal at the end",
    )
    p.add_argument(
        "--failed",
        type=int,
        default=5,
        help=(
            "Number of failed attempts before the successful one "
            "(default: 5, matches the ato_failure_threshold default)"
        ),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log = setup_logging("account-takeover", level=args.log_level)

    log.info(
        "Starting account-takeover attack: username=%s failed=%d delay=%.2fs",
        args.username, args.failed, args.delay,
    )

    client = HttpClient(args.target_url, logger=log)

    recorder = None
    if not args.no_record:
        recorder = GroundTruthRecorder(
            scenario="account_takeover",
            target_base_url=args.target_url,
            notes=f"username={args.username} failed={args.failed}",
        )
        recorder.expect(rule="account_takeover", severity="critical")
        # Ako broj neuspeha pređe brute_force prag (5), očekuj da okine
        # i to pravilo.
        if args.failed >= 5:
            recorder.expect(rule="brute_force", severity="high")

    # Faza 1: neuspeli login-i
    for i in range(1, args.failed + 1):
        password = f"wrong_{i}"
        try:
            response = client.post(
                "/login",
                data={"username": args.username, "password": password},
            )
        except Exception as exc:
            log.error("Failed login %d errored: %s", i, exc)
            sleep_with_jitter(args.delay)
            continue

        log.info("Failed login %d: %d", i, response.status_code)
        if recorder:
            recorder.action(
                "failed_login",
                target=args.username,
                status_code=response.status_code,
            )
        sleep_with_jitter(args.delay)

    # Faza 2: proboj
    log.info("Attempting successful login...")
    try:
        response = client.post(
            "/login",
            data={"username": args.username, "password": args.password},
        )
    except Exception as exc:
        log.error("Breakthrough request failed: %s", exc)
        if recorder:
            recorder.close()
        return 1

    log.info("Breakthrough: %d", response.status_code)
    if recorder:
        recorder.action(
            "successful_login" if response.status_code == 200 else "failed_login",
            target=args.username,
            status_code=response.status_code,
        )

    if response.status_code != 200:
        log.warning(
            "Breakthrough did NOT succeed (got %d). Was the password correct?",
            response.status_code,
        )

    if recorder:
        path_out = recorder.close()
        log.info("Ground truth written to %s", path_out)

    return 0


if __name__ == "__main__":
    sys.exit(main())