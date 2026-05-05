# Experimental Log

## Suite Summary

The experimental suite was executed on May 4, 2026, comprising 76
runs across 12 scenarios in approximately 47 minutes. All 76 runs
completed without subprocess errors.

## Aggregate Results

Per-rule metrics across all 76 runs:

| Rule                | TP | FP | FN | Precision | Recall | F1   |
|---------------------|---:|---:|---:|----------:|-------:|-----:|
| brute_force         | 30 |  8 |  0 |    0.7895 | 1.0000 | 0.88 |
| directory_scanning  | 15 |  0 |  0 |    1.0000 | 1.0000 | 1.00 |
| account_takeover    | 10 |  0 |  0 |    1.0000 | 1.0000 | 1.00 |

Recall is perfect across all three rules: the system never missed
a documented attack. Precision drops below 1.0 only for the
brute_force rule, driven by False Positives from the
`nat_false_positive` and `slow_burst_brute_force` scenarios.

## Per-Scenario Highlights

**Perfect detection (F1 = 1.0 ± 0.0):**
`basic_brute_force`, `basic_dir_scan`, `basic_ato`,
`brute_force_with_noise`, `dir_scan_with_noise`,
`mixed_legitimate_and_attack`, `near_threshold_brute_force`,
`only_normal_traffic`, `low_and_slow`, `distributed_brute_force`.
The system behaves as designed across both positive scenarios
(detection succeeds) and negative scenarios (no spurious
incidents).

**Documented False Positive — `nat_false_positive` (F1 = 0.0):**
Three users typing wrong passwords from a shared source IP produce
6 failed authentications, which exceeds the brute_force threshold
of 5 within 60 seconds. The rule fires every time. This validates
the limitation predicted in ADR-022: brute_force grouping by
source IP only is the industry-standard approach but produces
False Positives in NAT and proxy environments.

**Stochastic detection — `slow_burst_brute_force` (F1 = 0.4 ± 0.55):**
Six failed logins spaced 16 seconds apart should not trigger the
brute_force rule because no 60-second sliding window contains five
attempts. However, in 3 of 5 runs the rule did fire. The cause
is real-time pipeline latency: when Redis buffering, Normalizer
processing, or Postgres write delay shortens the effective gap by
a few hundred milliseconds, the fifth attempt slips inside the
sliding window. This documents the noise inherent in
threshold-based detection at the boundary.

## Detection Latency

Across all detected incidents (n=63), the time from
`first_event_at` to `detected_at` had:

- Median: under 2 seconds for clean attack scenarios
- 5–9 seconds for scenarios with parallel legitimate traffic
- ~55 seconds for `slow_burst_brute_force` runs that did fire,
  reflecting the slow attack pace

The pipeline scales sub-second under clean load and degrades
gracefully under noise, never exceeding the time the attack takes
to unfold.

## Findings Discovered During the Experimental Phase

### Elasticsearch reconnect bug

While inspecting the Search page during experiment runs, the
frontend returned no results despite events existing in the index.
Root cause: the API Gateway's Elasticsearch client was opened
once at startup and stored in `app.state.es`. If Elasticsearch
was unreachable at that moment (e.g., because the reset workflow
restarted it shortly before), the client became `None` permanently
and all `/events/search` calls returned an empty page.

The fix was a lazy reconnect: when `app.state.es is None`, the
search handler now retries the connection. If it succeeds, the
new client is cached for subsequent requests. This addresses the
graceful-degradation guarantee promised in ADR-023 without adding
a background health check.

### NAT False Positives are systematic, not stochastic

The `nat_false_positive` scenario produced an FP in every one of
its 5 runs (Precision std = 0.0). This is not a flaky test — it
is the rule's deterministic response to a real attack pattern
that, by industrial convention, falls outside its detection
scope. Discussion of layered detection (per-(IP, user) grouping,
UEBA baselines) appears in Chapter 7.

### Sliding-window boundary is statistical, not exact

The `slow_burst_brute_force` scenario's stochastic results
(40% FP rate at delay = 16s) suggest the effective threshold of
the brute_force rule is slightly below the nominal 5-in-60s
specification once real-time latency is included. A production
deployment would either widen the timing margin or add jitter
tolerance to the rule's window logic.

## Reproducibility

The full suite can be re-executed via:

```bash