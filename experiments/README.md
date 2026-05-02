# Attack Simulation Framework

Reproducible attack scenarios for evaluating the SIEM platform.
Used in Week 11 to compute Precision, Recall, and F1 metrics for
each correlation rule.

## Layout

    experiments/
    ├── attacks/           Individual attack scripts (CLI tools)
    │   ├── base.py        Shared HTTP client and ground-truth recorder
    │   ├── brute_force.py
    │   ├── directory_scan.py
    │   ├── account_takeover.py
    │   └── traffic_normal.py
    ├── scenarios/         YAML scenario definitions
    │   ├── basic_brute_force.yaml
    │   ├── basic_dir_scan.yaml
    │   ├── basic_ato.yaml
    │   ├── brute_force_with_noise.yaml
    │   ├── dir_scan_with_noise.yaml
    │   ├── only_normal_traffic.yaml      (control group, expects 0)
    │   ├── low_and_slow.yaml             (Future work, expects 0)
    │   └── distributed_brute_force.yaml  (Future work, expects 0)
    ├── runs/              Ground-truth JSON output (gitignored)
    ├── run_scenario.py    Scenario orchestrator
    └── requirements.txt

## Setup

    pip install -r experiments/requirements.txt --break-system-packages

The platform must be running:

    docker compose up -d

## Running a scenario

A scenario is a YAML file describing one experiment: which attack
scripts run, in what order, with what arguments, and what the SIEM
should detect.

Run a single scenario:

    cd experiments
    python run_scenario.py scenarios/basic_brute_force.yaml --reset-db

`--reset-db` wipes events and incidents tables before the run so
each experiment starts from a clean slate.

The orchestrator writes a consolidated ground-truth JSON to `runs/`,
capturing run id, start/end times, expected incidents, and notes.
This is the input to the metric calculation in Week 11.

## Running an individual attack script

Each attack script is also runnable on its own. Useful for ad-hoc
demos or for crafting new scenarios:

    cd experiments/attacks

    # Brute force against admin
    python brute_force.py --username admin --attempts 8 --delay 0.3

    # Brute force followed by a successful breakthrough (ATO)
    python brute_force.py --username alice --attempts 5 \
        --reveal-password 'Wonderland2024!'

    # Directory scan with built-in wordlist
    python directory_scan.py --paths-count 30 --delay 0.1

    # Account takeover (cleaner than brute_force --reveal)
    python account_takeover.py --username bob --password 'BuilderBob#42'

    # 60s of normal traffic (control group)
    python traffic_normal.py --duration 60

When called by the orchestrator the scripts always receive
`--no-record` so only the orchestrator's consolidated record is kept.
Run them with `--no-record` manually if you do not want individual
ground-truth files cluttering `runs/`.

## Source-IP spoofing

`HttpClient` accepts a `--spoof-ip` flag which sends a custom
`X-Forwarded-For` header. The webapp reads this header to determine
the source IP, so a single host can simulate traffic from multiple
attacker IPs.

When using `--spoof-ip`, the target URL must point directly at the
webapp on port 9000. Going through Nginx (port 8080) causes Nginx to
overwrite the header.

`traffic_normal.py` extends this with `--spoof-ip-base BASE`. Each
known account is mapped to its own IP starting from `BASE + 1`. The
control-group scenario uses this so multiple legitimate users do not
collapse into a single IP and accidentally trigger threshold rules.

## Scenarios in detail

### basic_*.yaml — sanity tests
Pure attacks with no background noise. Each attack should produce
exactly the incidents listed in `expected_incidents`. Used to verify
that the detection pipeline works end-to-end before adding noise.

### *_with_noise.yaml — realistic conditions
The same attacks running in parallel with `traffic_normal.py`.
Verifies that legitimate user activity does not contribute to or
suppress attack detection.

### only_normal_traffic.yaml — control group
60 seconds of legitimate user activity, no attack. Any incident
produced here is a False Positive in the metrics.

The scenario uses `--spoof-ip-base 10.0.1.0` so each user gets a
distinct simulated IP. Without this, three users typo-ing their
passwords on the same shared IP would accumulate into the
brute_force or account_takeover threshold — a NAT artifact, not a
true detection. See ADR-021 for the discussion.

### low_and_slow.yaml — known False Negative
Four failed login attempts spread over 60 seconds, never crossing the
brute_force threshold of 5 within 60s. The SIEM is expected to NOT
detect this — it is a documented limitation of threshold-based rules.
Future work mitigations: UEBA, dynamic baselines, longer aggregation
windows.

### distributed_brute_force.yaml — known False Negative
Five attacker IPs each try 4 failed logins against `admin`. Total of
20 failed authentications, but no individual IP crosses the per-IP
threshold. brute_force groups by source IP (industry standard) so
this attack pattern goes undetected. Documented in Chapter 7 (Future
work) as a candidate for layered detection or per-username sub-rules.

## Output: ground-truth JSON

Every successful run writes one JSON file to `runs/`:

    {
      "run_id": "20260502T122026Z-da9d7b",
      "scenario": "basic_brute_force",
      "scenario_file": "scenarios/basic_brute_force.yaml",
      "started_at": "2026-05-02T12:20:26.638Z",
      "ended_at":   "2026-05-02T12:20:30.110Z",
      "target_base_url": "http://localhost:8080",
      "expected": [
        { "rule": "brute_force", "severity": "high", "min_count": 1 }
      ],
      "description": "..."
    }

Week 11's `compute_metrics.py` reads these files, queries the API
Gateway for incidents created in `[started_at, ended_at]`, and
compares against `expected` to emit Precision / Recall / F1.

## Adding a new scenario

1. Pick a unique `name`. Keep `expected_incidents` honest — say
   exactly what the SIEM should detect, including `[]` for negative
   tests.
2. List `steps` as `attack` (one script), `wait` (sleep N seconds), or
   `parallel` (multiple children running concurrently).
3. Test it: `python run_scenario.py scenarios/<your>.yaml --reset-db`.
4. Verify the resulting incidents in Postgres or the dashboard match
   what you expected.

A useful template:

    name: my_scenario
    description: |
      One-paragraph plain-text explanation of what is being tested
      and why. This text is preserved in the ground-truth JSON.

    expected_incidents:
      - rule: brute_force
        severity: high
        min_count: 1

    steps:
      - type: attack
        script: brute_force.py
        args:
          - --username
          - admin
          - --attempts
          - "8"

      - type: wait
        seconds: 3