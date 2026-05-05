# Experimental Methodology

## Goal

Quantitatively evaluate the SIEM platform's detection accuracy through
reproducible attack simulations, measuring Precision, Recall, and F1
score per detection rule.

This document describes the experimental setup. Findings are
recorded in `experiments_log.md`.

## Definitions

For each (rule, run) pair, the system's output is classified into one
of four buckets:

- **True Positive (TP)** — Scenario expected an incident for rule X
  and the system produced one.
- **False Positive (FP)** — Scenario expected no incident for rule X
  but the system produced one.
- **False Negative (FN)** — Scenario expected an incident for rule X
  but the system produced none.
- **True Negative (TN)** — Scenario expected no incident and the
  system produced none.

Metrics are derived per rule by aggregating across all 76 runs:

Precision = TP / (TP + FP)
Recall    = TP / (TP + FN)
F1        = 2 * Precision * Recall / (Precision + Recall

For negative scenarios (`expected_incidents: []`) Precision is
defined as 1.0 when FP=0 to avoid the 0/0 case.

## Experimental Framework

The framework consists of three layers:

**Attack scripts** under `experiments/attacks/` — standalone CLI tools
that simulate one attack pattern each (`brute_force.py`,
`directory_scan.py`, `account_takeover.py`, `traffic_normal.py`).

**Scenarios** under `experiments/scenarios/*.yaml` — declarative
descriptions of an experimental run. Each scenario specifies expected
incidents, individual attack steps (sequential or parallel), and
inter-step waits. Twelve scenarios are defined: three positive
("basic") attacks, two attacks mixed with legitimate traffic, four
negative scenarios that should not trigger detection, two boundary
cases that exercise threshold and sliding-window edge logic, and one
realistic mixed scenario.

**Orchestrators** — `run_scenario.py` executes a single scenario and
captures both ground truth (the expected outcome from the YAML) and
the actual incidents produced (queried from the API Gateway). Both
are written to `experiments/runs/<id>.json`. `run_all.py` runs the
full suite according to `run_all.config.yaml`.

## Database Reset Between Runs

Every run begins with `--reset-db`, which performs a full pipeline
reset:

1. `TRUNCATE incidents, events RESTART IDENTITY CASCADE` on Postgres
2. `DELETE` all `events-*` indices on Elasticsearch
3. `FLUSHDB` on Redis (clearing both streams and consumer groups)
4. `docker compose restart` of the Normalizer and Correlator
   services to recreate consumer groups and clear in-memory sliding
   window state
5. A 3-second wait for services to reconnect

The reset is required because:

- The Alert Manager's deduplication window (5 minutes per ADR-016)
  would otherwise merge incidents from successive runs into a single
  database row, producing False Negatives for every run after the
  first.
- The Correlator's in-memory sliding window state (per ADR-013)
  would carry events from one run into the next, biasing thresholds.

## Run Lifecycle

After a scenario completes its YAML steps, `run_scenario.py` waits
3 additional seconds and then queries the API Gateway for incidents
detected within the run's time window (with a 5-second tolerance on
each side). The result is embedded in the ground-truth JSON as
`actual_incidents`. This locks in the run's result before the next
reset wipes it from the database.

`compute_metrics.py` reads `actual_incidents` directly from each
ground-truth file rather than re-querying the API. This decouples
metric computation from the live database state and makes the metric
pipeline deterministic.

## Match Logic

A scenario's `expected_incidents` list and the run's `actual_incidents`
list are matched at the **rule_name** level using `min_count`
semantics: an expected entry counts as a True Positive if the
detected incidents include at least `min_count` incidents with the
same `rule_name`, regardless of source IP, target user, or other
incident metadata.

This choice avoids brittleness from Docker network artifacts (every
incident's `source_ip` is `172.18.0.1` by default) while still
detecting both TPs and FPs accurately.

## Run Counts

The 76 runs are distributed asymmetrically across scenarios to
balance statistical power against execution time:

- 10 runs each for the four most important scenarios
  (`basic_brute_force`, `basic_dir_scan`, `basic_ato`,
  `only_normal_traffic`)
- 5 runs each for medium-stochasticity scenarios
  (`brute_force_with_noise`, `dir_scan_with_noise`,
  `near_threshold_brute_force`, `slow_burst_brute_force`,
  `nat_false_positive`, `mixed_legitimate_and_attack`)
- 3 runs each for fully deterministic negative scenarios
  (`low_and_slow`, `distributed_brute_force`)

Total execution time: approximately 47 minutes sequentially.

## Detection Rule Parameters

The three correlation rules use industrial-standard thresholds (per
ADR-014):

- **brute_force**: 5 failed authentications within 60 seconds, grouped
  by source IP (per ADR-022).
- **directory_scanning**: 20 distinct 404 paths within 60 seconds,
  grouped by source IP.
- **account_takeover**: 5 failed authentications followed by a
  successful login within 600 seconds, grouped by (source IP, user).

These values are not tuned on the experimental data; they were fixed
during Week 8 based on industry SOC playbooks before any run was
executed.

## Output Artifacts

Each suite execution produces:

- `experiments/runs/*.json` — one ground-truth JSON per run
- `experiments/results/per_run.jsonl` — per-run computed metrics
- `experiments/results/per_rule.csv` — aggregated metrics per rule
- `experiments/results/per_scenario.csv` — mean ± std per scenario
- `experiments/run_all.log` — timestamped audit log of the suite
- `experiments/results/plots/*.png` — six plots for thesis Chapter 6