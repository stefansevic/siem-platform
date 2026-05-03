# Architecture Decision Records (ADR)

This document captures key architectural and technical decisions made during
the development of the SIEM-like platform. Each entry follows a lightweight
ADR format: **context**, **decision**, **consequences**.

---

## ADR-001: Microservice architecture with Docker Compose

**Date:** 2026-04-25

**Context:** The platform must be modular per the project specification —
separate concerns for ingestion, normalization, correlation, and alerting.
The development environment must be reproducible across Windows (development)
and macOS (presentation) machines.

**Decision:** Adopt a microservice architecture with five independent FastAPI
services orchestrated by Docker Compose. Each service owns a single
responsibility and communicates with others via a shared Docker network.

**Consequences:**
- ✅ Each component can be developed, tested, and scaled independently.
- ✅ Reproducible environment across machines via `docker compose up`.
- ✅ Easy to demonstrate during defense — judges see real distributed system.
- ⚠️ More moving parts than a monolith; requires careful service orchestration
  via healthchecks and `depends_on` conditions.

---

## ADR-002: Python 3.11 + FastAPI as the backend stack

**Date:** 2026-04-25

**Context:** Backend technology was free choice (excluding .NET). The student
has prior Python experience and a 10-week timeline with limited weekly hours.

**Decision:** Use Python 3.11 with FastAPI for all backend services.

**Consequences:**
- ✅ Fast development; rich library ecosystem for log parsing and async I/O.
- ✅ Pydantic provides built-in validation aligned with ECS schema modeling.
- ✅ Type hints improve code quality and assist with thesis documentation.
- ⚠️ Python is slower than Go or Rust; acceptable for academic prototype.

---

## ADR-003: Redis Streams for inter-service messaging

**Date:** 2026-04-25

**Context:** Services must communicate asynchronously to provide buffering
during traffic spikes and to remain decoupled. Choices considered:
RabbitMQ, Apache Kafka, Redis Streams.

**Decision:** Use Redis Streams as the message broker. Two streams are
defined: `raw_logs` (ingestor → normalizer) and `normalized_events`
(normalizer → correlator).

**Consequences:**
- ✅ Single Redis container handles both messaging and ephemeral caching.
- ✅ Simpler operational footprint than Kafka for a prototype scale.
- ✅ Native support for consumer groups enables horizontal scaling later.
- ⚠️ Less feature-rich than Kafka (no built-in retention policies for years
  of data); acceptable as raw logs are short-lived in transit.

---

## ADR-004: PostgreSQL for persistent storage

**Date:** 2026-04-25

**Context:** Normalized events and generated incidents require long-term
storage with rich querying capabilities for the dashboard and reporting.

**Decision:** Use PostgreSQL 16 as the primary persistent datastore.
Two principal tables: `events` (normalized log events, ECS-compatible)
and `incidents` (generated alerts).

**Consequences:**
- ✅ Mature ACID-compliant store with excellent SQL query support.
- ✅ Indexable on ECS fields like `source_ip` and `@timestamp` for fast
  dashboard queries.
- ✅ Familiar to almost all developers and reviewers.
- ⚠️ Requires explicit schema migrations as the data model evolves.

---

## ADR-005: Elastic Common Schema (ECS) for normalization

**Date:** 2026-04-25

**Context:** Heterogeneous log sources (Nginx text logs, application JSON
logs) must be unified into a single schema so that correlation rules can
operate independently of the source format.

**Decision:** Adopt Elastic Common Schema (ECS) as the normalized event
schema. Use a curated subset of fields relevant to web-application
security (timestamp, event category/outcome, source IP, username,
HTTP status code, URL path).

**Consequences:**
- ✅ Industry-standard schema; recognizable to security professionals.
- ✅ Correlation rules become source-agnostic.
- ✅ Future integration with Elasticsearch (a stretch goal) is trivial.
- ⚠️ Requires explicit field mapping logic in the Normalizer service.

---

## ADR-006: Two log sources with intentionally different formats

**Date:** 2026-04-25

**Context:** The specification mandates at least two log sources. The choice
of formats directly affects the complexity and value of the Normalizer.

**Decision:** Use Nginx access logs (extended Combined Log Format, plain
text) and a custom FastAPI demo webapp (structured JSON, ECS-friendly
field names) as the two log sources. Both sit behind Nginx as a reverse
proxy in front of the demo webapp.

**Consequences:**
- ✅ Realistic production-like topology (web app behind reverse proxy).
- ✅ Demonstrates the Normalizer's value: regex parsing of plain text and
  JSON parsing produce identically shaped ECS events.
- ✅ Different source.ip semantics (Nginx sees client IP, app sees Nginx IP
  unless `X-Real-IP` is forwarded) provides a realistic challenge.
- ⚠️ Requires Nginx configuration to forward original client IP.

---

## ADR-007: Cross-platform development hygiene

**Date:** 2026-04-25

**Context:** Development on Windows (WSL2) with deployment demo on macOS
introduces line-ending and path-separator pitfalls. Issues caught early
are far cheaper than issues caught the week before defense.

**Decision:** Enforce LF line endings via `.gitattributes`, run all
development inside the WSL2 filesystem (not the Windows-mounted drive),
use only forward-slash paths and relative paths in `docker-compose.yml`,
and verify the project boots cleanly on macOS at the end of week 4.

**Consequences:**
- ✅ Avoids the most common cross-platform Docker pitfalls preemptively.
- ✅ Identifies portability issues with weeks to spare before the defense.

---

---

## ADR-008: "Dumb Ingestor" pattern — no parsing or validation in the entry point

**Date:** 2026-04-26

**Context:** The Ingestor is the first component touching every log
that enters the system. Two design philosophies are possible:
either parse and validate at the entry point, or forward raw data
unchanged and parse downstream.

**Decision:** Adopt the "dumb pipe" pattern. The Ingestor performs no
parsing, validation, schema enforcement, or filtering on payloads.
It only attaches metadata (source identifier, format hint, receive
timestamp) and publishes the raw bytes to the Redis stream `raw_logs`.

**Consequences:**
- ✅ The Ingestor is fast: a single log entry costs only a JSON encode
  and a Redis XADD.
- ✅ The Ingestor is robust: parser bugs in downstream services cannot
  crash the entry point or cause log loss; bad logs sit in the stream
  and can be reprocessed once the parser is fixed.
- ✅ The Ingestor and Normalizer scale independently. Operators can run
  3 Ingestor replicas behind a load balancer if intake spikes, or 5
  Normalizer replicas if parsing becomes the bottleneck.
- ✅ Aligns with industry practice: Logstash, Fluent Bit, Vector,
  Splunk Universal Forwarder, AWS Kinesis Agent — all follow the
  "ship raw, parse later" pattern.
- ⚠️ One edge of complexity moves downstream: the Normalizer must
  defend against malformed or hostile inputs that the Ingestor would
  otherwise have rejected.

---

## ADR-009: Push and pull ingestion in a single service

**Date:** 2026-04-26

**Context:** The platform must accept logs from two source styles
without coupling source services to a specific ingestion mechanism.

**Decision:** The Ingestor exposes both a **push** path (HTTP
`POST /logs` for applications that emit JSON) and a **pull** path
(file tailer for the Nginx access log) inside the same service.

**Consequences:**
- ✅ Application services like the demo webapp ship logs synchronously
  via fire-and-forget HTTP without any disk involvement.
- ✅ Legacy or third-party components like Nginx, which only know how
  to write to a file, are still ingested without modification.
- ✅ Both paths converge on the same `RawLogMessage` shape, so the
  Normalizer downstream remains source-agnostic.
- ⚠️ The file tailer assumes Nginx writes to a real file, not the
  default `/dev/stdout` symlink — this required a small Dockerfile
  customization and is documented in the runbook.

---

## ADR-010: Layered Normalizer architecture with pure-function core

**Date:** 2026-04-27

**Context:** The Normalizer is the most complex service in the platform: it
parses two distinct log formats, maps them to ECS, persists to Postgres,
and republishes to a downstream stream. A naive implementation would
intertwine I/O and business logic, making the service hard to test and
hard to evolve.

**Decision:** Split the Normalizer into a layered architecture where the
core normalization logic is composed of pure, I/O-free functions:

- **`parsers.py`** — `parse_nginx_siem_combined()` and
  `parse_demo_webapp_json()` take a payload string and return a
  `ParsedFields` dataclass. No Redis, no Postgres, no network.
- **`mapper.py`** — `normalize()` composes a parser with a deterministic
  mapping from `ParsedFields` to `ECSEvent`. Still I/O-free.
- **`idempotency.py`** — `compute_idempotency_key()` returns a SHA-256
  hex digest of the raw envelope. Pure function.
- **`db.py`** — async SQLAlchemy `EventWriter`, the only Postgres I/O.
- **`redis_consumer.py`** — `NormalizerConsumer`, the only Redis I/O.
- **`main.py`** — orchestration, lifecycle, signal handling.

**Consequences:**
- ✅ 49 unit tests for parsers, mapper, and idempotency run in ~0.05s
  without any container, giving instant feedback during development.
- ✅ Integration tests for the I/O layer (db.py, redis_consumer.py) are
  small and focused; they do not have to re-test parsing semantics.
- ✅ Replacing the storage backend (e.g. Elasticsearch as a stretch goal)
  only changes `db.py`; parsing and mapping are untouched.
- ✅ The boundary makes the service understandable to a reader following
  the data flow top-down: payload → ParsedFields → ECSEvent → DB row.
- ⚠️ Slightly more files than a monolithic `normalizer.py`; trivial
  navigation cost compared to the testing and refactoring benefits.

---

## ADR-011: Dead-letter stream for malformed payloads

**Date:** 2026-04-27

**Context:** The Normalizer is a downstream consumer of arbitrary log
content forwarded by the "dumb" Ingestor (see ADR-008). Malformed
payloads — broken JSON, Nginx lines that no longer match the regex,
truncated entries — are guaranteed to occur eventually. Three responses
are possible: (1) skip silently, (2) crash the consumer loop,
(3) quarantine the bad entry and continue.

**Decision:** Adopt the dead-letter pattern. When a `ParseError`,
`MappingError`, or invalid envelope is detected, the entry is published
to a separate Redis stream `dead_letter_logs` with the original
payload and a human-readable failure reason, then acked on the source
stream so the consumer keeps moving.

**Consequences:**
- ✅ A single poison-pill payload cannot stop the pipeline. The Normalizer
  keeps processing legitimate traffic even when an attacker injects
  deliberately malformed input.
- ✅ Bad entries are preserved verbatim for forensic analysis, not
  silently dropped. An operator can later inspect `dead_letter_logs`
  to understand parser drift or attack attempts.
- ✅ The dead-letter stream is capped via Redis MAXLEN to prevent
  unbounded memory growth.
- ✅ Distinct failure modes are categorized in the reason field
  (`envelope:`, `normalize:`), simplifying triage.
- ⚠️ Two streams to monitor instead of one. Acceptable in exchange for
  the operational resilience.

---

## ADR-012: Storage-layer idempotency via deterministic SHA-256 keys

**Date:** 2026-04-27

**Context:** The Normalizer is an at-least-once consumer of the Redis
`raw_logs` stream. Several scenarios produce duplicate deliveries:
the Normalizer crashes after persisting an event but before XACK; the
Ingestor restarts and re-tails an Nginx log line; an operator manually
replays a stream segment. Without protection, each duplicate inflates
the count of failed logins, polluting brute-force detection and other
correlation rules.

**Decision:** Compute a deterministic idempotency key as
`SHA-256(source ⨁ format ⨁ payload)` for every raw envelope and store
it in a `NOT NULL UNIQUE` column on the `events` table. All inserts use
`INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`, making the
storage layer the single source of truth for "have we seen this?".

**Consequences:**
- ✅ Exactly-once persistence is guaranteed by the database, not by
  application logic. No race condition between "check then insert"
  can leak a duplicate.
- ✅ The key is deterministic from the raw input, so duplicates from
  any retry path (Normalizer crash, Ingestor crash, manual replay)
  collapse into the same row.
- ✅ ASCII unit-separator bytes between source/format/payload prevent
  cross-source collisions where two different sources happen to share
  identical byte sequences.
- ✅ The collision risk of SHA-256 is cryptographically negligible at
  the volumes this platform will ever see.
- ⚠️ Two genuinely identical events from the same source within the
  same second (e.g. two clients producing byte-identical Nginx lines)
  collapse into one row. Acceptable trade-off in this domain: the loss
  of one observation matters far less than a single duplicate that
  poisons brute-force counters.

---

---

## ADR-013: In-memory state for the Correlation Engine

**Date:** 2026-04-28

**Context:** Correlation rules require sliding-window state per subject
(e.g. one window of failed authentications per source IP). Two
implementations were considered: a Redis-backed store (sorted sets keyed
by `(rule, subject)` with timestamps as scores) versus an in-process
dictionary of Python deques.

**Decision:** Use in-memory state. The engine holds
`Dict[(rule_name, subject_key), SlidingWindow]` where each SlidingWindow
is a `collections.deque` of timestamped entries. Stale windows are
pruned periodically using stream time (the latest event's timestamp),
not wall-clock.

**Consequences:**
- ✅ Sub-microsecond per-event dispatch with no network round-trip,
  enabling realistic single-replica throughput for the demo.
- ✅ The whole correlation jezgro is testable as pure code: 51 unit
  tests run in milliseconds with no Redis required.
- ✅ Restart of the Correlator clears the state. For a defense demo this
  is acceptable: the simulation script generates its own attack and the
  detector consumes it within seconds. For a production deployment the
  state would migrate to Redis sorted sets — listed in Chapter 7
  (Future work) of the thesis.
- ⚠️ State is not shared across replicas. Horizontal scaling would
  require partitioning subjects by a consistent hash so each replica
  owns a disjoint slice — not in scope for this iteration.

---

## ADR-014: Stream-time semantics for sliding windows

**Date:** 2026-04-28

**Context:** Sliding-window logic needs a definition of "now". Two
options exist: (a) wall-clock time read from `datetime.now()` whenever
the rule evaluates, or (b) the timestamp of the most recently observed
event ("stream time"). Both are commonly used; the choice affects
testability, replay correctness, and behavior under clock skew.

**Decision:** Adopt stream-time semantics. The engine treats each new
event's `timestamp` field as the current instant, advancing all windows
to that point. `prune_stale()` is also called with stream time. No code
path inside parsers, mapper, rules, engine, or windows reads
`datetime.now()`.

**Consequences:**
- ✅ Tests are deterministic. Every test feeds synthetic timestamps and
  asserts exact behavior at every boundary; no `freezegun` or
  monkey-patching of `datetime.now`.
- ✅ Replay is correct. Re-feeding old events from the dead-letter
  stream or from a Postgres backfill produces the same incidents as
  the original run.
- ✅ Resilient to clock skew between log sources and the Correlator
  host. The rule fires on the relationship between event timestamps,
  not on the local clock.
- ⚠️ A truly idle subject does not age out until either a new event
  arrives or the engine's periodic `prune_stale()` is invoked. The
  consumer triggers `prune_stale()` after every batch, so memory
  pressure remains bounded under realistic traffic.

---

## ADR-015: Correlator emits every trigger; deduplication lives in the Alert Manager

**Date:** 2026-04-28

**Context:** When a brute-force rule with threshold 5 sees a sixth, then
a seventh failure, the rule technically "fires" again with `event_count
= 6` and `event_count = 7`. The same applies to directory scanning past
its distinct-path threshold. A naive consumer of the incidents stream
would see one "attack" produce many incident records.

**Decision:** Correlator does NOT deduplicate. Every rule trigger is
published verbatim to the `incidents` stream with its current
`event_count`, contributing event IDs, and a fresh incident UUID. The
Alert Manager (Week 5) is responsible for collapsing related triggers
into a single open incident, updating its `event_count` and
`last_event_at` on subsequent fires from the same `(rule, source_ip)`.

**Consequences:**
- ✅ Correlator stays simple and stateless beyond its sliding windows.
  It does not need to remember "did I already alert about this attack
  in the last N minutes?"
- ✅ The deduplication policy is a single-service concern. Tuning the
  policy (silence window length, escalation rules, severity bumps) is
  done in the Alert Manager without touching the detection logic.
- ✅ The full audit trail is preserved on the stream. Every individual
  trigger remains inspectable for forensic analysis.
- ⚠️ The raw incidents stream is verbose: a 6-failure brute-force
  produced 2 events, an 11-path directory scan produced 20 events in
  the live demo. This is expected behavior; the Alert Manager will
  reduce these to one open incident per attack episode in the
  `incidents` Postgres table.

---


---

## ADR-016: Time-based silence window for incident deduplication

**Date:** 2026-04-29

**Context:** The Correlator emits an incident every time a rule's
threshold is reached. A six-failure brute-force produces two triggers
(at the 5th and 6th event); an eleven-path directory scan produced
fifteen triggers in the live demo. Persisting each trigger as a fresh
row would flood operators with one apparent attack expressed as dozens
of records.

Three approaches were considered:
    A. Silence window: treat all triggers within N minutes of the last
       activity for the same (rule, source_ip) as the same incident.
    B. Status-driven: keep merging until the operator marks the
       incident as closed/acknowledged.
    C. Use the rule's own time window as the silence period.

**Decision:** Adopt option A with a default 5-minute silence window
(configurable via ALERT_SILENCE_WINDOW_SECONDS). The Alert Manager
queries `incidents` for an open row matching (rule_name, source_ip)
whose last_event_at is within the silence window relative to the new
trigger; if found, it merges; otherwise it inserts a fresh incident.

**Consequences:**
- ✅ One incident row per attack episode. The directory-scan demo
  collapsed 15 triggers into a single row with event_count=22.
- ✅ Tunable per environment without a code change. A noisier
  environment can shorten the window; a quieter one can extend it.
- ✅ A new attack from the same IP after several minutes of silence
  starts a fresh incident — the operator can distinguish "ongoing"
  from "returned" attackers.
- ✅ Status-driven dedup (B) was rejected because in our flow no one
  closes incidents during a defense demo, which would cause every
  future attack to merge into the very first one indefinitely.
- ⚠️ The silence window is global across rules. If different rules
  needed different windows, this would be configured per-rule. Out
  of scope for the prototype.
- ⚠️ Severity preservation: a merged trigger never downgrades the
  incident's severity. An operator-set CRITICAL stays CRITICAL even
  if a low-severity duplicate arrives later.

---

## ADR-017: Composite notifier pattern with isolated failure handling

**Date:** 2026-04-29

**Context:** The project specification mentions both webhook and
console notifications. Two implementations are needed, and they may
share or diverge in delivery semantics over time. A naive design
hard-codes both calls inline; a flexible one introduces an abstraction
that can grow with future integrations (Slack, PagerDuty, email).

**Decision:** Define a `Notifier` abstract base class with a single
`async notify(incident, was_merged)` method. Provide three concrete
implementations:
    - ConsoleNotifier (always active; structured JSON log line).
    - WebhookNotifier (opt-in; HTTP POST to a configured URL).
    - CompositeNotifier (dispatches to a list of notifiers concurrently
      via asyncio.gather, isolating each from failures in the others).

**Consequences:**
- ✅ The Alert Manager wires its notifier composition once at startup;
  the consumer loop is unaware of how many or which notifiers exist.
- ✅ Adding a third notifier (e.g. for Slack) is a one-file change.
- ✅ A failing notifier — webhook timeout, malformed Slack payload —
  cannot block the others or the consumer loop. Persistence has
  already happened by the time notify() runs, so notification is
  treated as best-effort.
- ✅ Webhook is genuinely opt-in: setting ALERT_WEBHOOK_URL=https://...
  enables it; leaving it unset means the service runs with console
  alerts only and no failed delivery attempts.
- ⚠️ Notification ordering is not guaranteed across notifiers. Each
  fires concurrently. For the prototype this is acceptable; an
  explicit order requirement would change the dispatch from gather
  to a sequential loop.

---

---

## ADR-018: Hybrid frontend deployment — dev server in development, Docker for demos

**Date:** 2026-04-30

**Context:** The dashboard is a Vite + React app. Two natural ways to
run it during the project: run `npm run dev` directly on the host
(fast hot reload, native debugging) or build a Docker image (production
parity, single `docker compose up` for the whole stack). Each excels
where the other is awkward.

**Decision:** Support both. During day-to-day development, the
frontend runs via `npm run dev` from the host on port 5173 with hot
reload. For demos, the cross-platform check, and any production-like
verification, the frontend runs in a Docker container on port 3000,
built via the multi-stage Dockerfile (Vite build → nginx serve).

**Consequences:**
- ✅ Iteration speed during development is sub-second; the Docker image
  takes ~90s to build, which is acceptable since it is built rarely.
- ✅ The Docker image is small (~25 MB) because the final stage only
  carries the compiled static assets and nginx, not Node.js or the
  source tree.
- ✅ A single `docker compose up` brings the whole stack online, which
  is exactly what an external reviewer needs.
- ✅ The frontend container's nginx is configured for SPA routing
  (`try_files $uri $uri/ /index.html`), so deep links like
  `/incidents/<id>` resolve correctly when the user reloads.
- ⚠️ The API base URL is currently baked into the frontend at build
  time (`http://localhost:8005`). Hosting the dashboard on a different
  origin will require parameterizing this; out of scope for the
  prototype (see ADR-019 for the dev/demo trade-off).

---

## ADR-019: HTTP polling, not WebSocket, for live data

**Date:** 2026-04-30

**Context:** A SIEM dashboard needs to feel "live" — incidents should
appear within seconds, stat counters should update on their own.
Two implementation patterns are common: HTTP polling at a short
interval (5 s typical), or a server push channel (WebSocket or
Server-Sent Events) where the gateway streams updates as they happen.

**Decision:** Use HTTP polling at 5-second intervals. A custom
`usePolling` hook drives every live view (Dashboard, Incidents, Events).
The API Gateway exposes only conventional REST endpoints; no WebSocket
or SSE channel.

**Consequences:**
- ✅ The Gateway stays a simple FastAPI app. No long-lived connections,
  no WebSocket lifecycle to manage, no Redis Pub/Sub fan-out.
- ✅ The frontend stays simple too. A single hook covers loading,
  error, and refresh state for every page.
- ✅ Five seconds of latency is invisible during a live demo. SOC
  dashboards from major vendors (Splunk, Datadog) default to similar
  intervals.
- ✅ Backwards compatible with future scale: the polling load is one
  request per page per 5s per user, which is trivial for the Gateway.
- ⚠️ Live event feed during a high-rate attack can lag by up to 5s.
  If true real-time becomes a requirement (live SOC operations rather
  than analyst review), an SSE endpoint that pushes new incident
  notifications would be a small follow-up — listed in Chapter 7
  (Future work) of the thesis.

---

---

## ADR-020: UI polish priorities for the operator dashboard

**Date:** 2026-04-30

**Context:** After the basic dashboard shipped (Week 6) it was clear
that several quality-of-life elements were missing — an operator
glancing at a SIEM screen needs reassurance that data is fresh,
context for empty states, and confirmation when their actions
landed. A list of seven candidate improvements was triaged.

**Decision:** Implement the six high-value polish items, skip the
seventh (light-theme toggle).

Implemented:
    - **Health indicator** in the sidebar (color-coded dot from a
      separate /health probe).
    - **Severity-coded badge** on the Incidents nav link with the
      open count.
    - **Refresh indicator** in every page header (spinning icon while
      polling, "Updated Xs ago" otherwise).
    - **Empty states** in Incidents and Events tables with iconography
      and guidance.
    - **Per-event table** inside the incident detail modal, replacing
      the bare list of UUIDs with what each contributing event
      actually was.
    - **Toast notifications** for triage actions (status update,
      errors).

Skipped: a light-theme toggle. Industry SIEM dashboards (Splunk,
Datadog, Sentry) default to dark and most do not offer a toggle, since
operators stare at the screen for hours. Estimated cost was 3-4 hours
of refactoring (parallel CSS variables, Recharts color overrides,
component-by-component verification) for a feature the demo audience
will not exercise.

**Consequences:**
- ✅ Each visible page now has fresh-data signaling, error feedback,
  and empty-state UX. The dashboard reads as a finished product
  rather than a prototype.
- ✅ The skipped item becomes a documented Future-work item in the
  thesis (Chapter 7) — "high-contrast and light-theme support for
  enterprise environments".
- ✅ The polish items use only existing dependencies (lucide-react,
  Tailwind utility classes). No new libraries, no bundle bloat.
- ⚠️ The toast component is in-house (no `react-toastify`, no
  `sonner`). Trade-off: smaller surface, fewer features. For the
  prototype's needs (success / error / info, single line of text)
  this is sufficient.

---

---

## ADR-021: Attack simulation framework with ground-truth bookkeeping

**Date:** 2026-05-02

**Context:** Manually triggering attacks with curl is good enough for
a live demo, but the project needs reproducible experiments to compute
Precision, Recall, and F1 in Week 11. Ad-hoc execution loses two things
metrics depend on: a precise time window for each run, and an explicit
record of what the operator intended the SIEM to detect. Without those,
the comparison "what did the system find vs what was expected" is
guesswork.

**Decision:** Build a small `experiments/` framework around three ideas:

1. **Attack scripts** as standalone CLI tools (`brute_force.py`,
   `directory_scan.py`, `account_takeover.py`, `traffic_normal.py`).
   Each accepts knobs over argparse and shares a base library
   (`HttpClient` wrapper, `GroundTruthRecorder`).
2. **Scenarios** as declarative YAML files in `scenarios/`. A scenario
   names its expected incidents up front and lists the steps that run
   sequentially or in parallel.
3. **Orchestrator** (`run_scenario.py`) which executes a scenario,
   captures start/end timestamps, and writes a single consolidated
   ground-truth JSON to `runs/`. Individual attack scripts are invoked
   with `--no-record` so the only persisted record per scenario is the
   orchestrator's.

Eight scenarios were defined: three "basic" (clean attacks), two with
parallel legitimate traffic, one control group, and two negative
controls (low-and-slow and distributed brute force) for documented
False Negatives.

**Consequences:**
- ✅ The same scenario can be replayed any number of times. Week 11's
  metric script reads each `runs/<id>.json`, asks the API Gateway for
  incidents created in that window, and compares against `expected`.
- ✅ Negative scenarios (`expected_incidents: []`) make False Positive
  measurement explicit. Without them the control group looks healthy
  by accident; with them, every "this should produce zero" claim is
  testable.
- ✅ Source-IP spoofing via `X-Forwarded-For` (with the target pointed
  at the webapp on port 9000) lets a single host simulate distributed
  attacks and per-user IP isolation in the control group.
- ⚠️ The framework runs on the same machine as the SIEM, so attacker
  and defender share a host. For a more rigorous evaluation a separate
  attack VM would be appropriate; out of scope for the prototype.
- ⚠️ Ground-truth runs accumulate in `runs/`. The directory is
  gitignored; it is the operator's responsibility to clean it up
  between experimental runs in Week 11.

---

## ADR-022: Account takeover requires same-user matching

**Date:** 2026-05-02

**Context:** During the construction of the control-group scenario in
Week 8 a False Positive was observed: 60 seconds of legitimate traffic
from three users sharing one source IP produced an `account_takeover`
incident. Looking at the trigger, the rule had counted failed logins
across all users on that IP — three of whom typed a typo and one of
whom logged in normally — and concluded a takeover. This was wrong as
a matter of definition: account takeover is the compromise of a
specific account, not "some failures and some success near each other".

**Decision:** The `account_takeover` rule now requires the failed
attempts and the successful login to target the same `user_name`. The
implementation filters `prior_failures` by
`e.user_name == event.user_name` inside `evaluate()`, and rejects
events whose `user_name` is unknown. Two new regression tests were
added covering the per-user logic.

**Consequences:**
- ✅ The control-group scenario now produces zero incidents over 60
  seconds of mixed traffic with three users sharing a source IP —
  exactly what the metrics demand.
- ✅ The rule now matches its English-language definition. A demo to
  the committee no longer requires "...except when..." caveats.
- ✅ The fix does not affect single-user attacks (basic_ato scenario
  still triggers normally) and preserves the brute_force + ATO
  combination on the same victim.
- ⚠️ The fix only addresses ATO. Brute-force still groups by source
  IP, which is the industry standard but is known to produce False
  Positives in NAT/proxy environments where many legitimate users
  share an IP. We chose not to switch brute_force to per-(IP, user)
  grouping because that would lose detection of credential stuffing
  attacks. The trade-off is documented as Future work in Chapter 7
  alongside layered detection and UEBA proposals.

---

---

## ADR-023: Elasticsearch dual-write for event search

**Date:** 2026-05-03

**Context:** Postgres handles transactional incident workflow well —
ACID for status changes, foreign keys for incident-event links — but
it is the wrong tool for free-text search and time-range aggregations
over an ever-growing log table. SOC operators need to answer questions
like "show me every authentication failure for user `bob` in the last
24 hours involving paths matching `/admin*`" in milliseconds, and a
LIKE query against a Postgres BIGINT-keyed events table does not
deliver that.

The thesis specification asks for "fast search across the event log"
which Postgres alone cannot honestly meet at scale.

**Decision:** Adopt a hybrid storage design.

- **Postgres** remains the source of truth for events and incidents.
  Every event written to Postgres still drives correlation rules,
  alert workflow, and dedup. No behavioural change for incidents.
- **Elasticsearch 8.13** is added as a parallel write target, used
  only for read-side search. The Normalizer service writes each new
  (non-duplicate) event to a daily index `events-YYYY.MM.DD` after
  the Postgres insert succeeds. Daily indexing follows Elastic's
  recommendation for time-series log data and makes retention a
  trivial `DELETE /events-2026.04.*`.
- An **index template** (`shared/elasticsearch_index.py`) is applied
  by the Normalizer at startup. Field types are pinned explicitly:
  IP addresses use `ip` (CIDR queries), timestamps use `date`,
  identifiers use `keyword`, free-text fields like `user_agent` use
  `text` with a `.keyword` sub-field for exact matching.
- A new endpoint **`GET /events/search`** in the API Gateway routes
  to ES. Filters mirror existing `/events` semantics; an extra `q`
  parameter does multi-field full-text matching.
- ES failures are **non-fatal** by default. The Normalizer logs the
  failure and keeps writing to Postgres; the API Gateway falls back
  to empty results with a warning. `ELASTICSEARCH_REQUIRED=true` can
  flip this for environments where degraded search is unacceptable.

**Consequences:**
- ✅ The architecture matches industrial SIEM practice (Elastic SIEM,
  Splunk, ELK stack): transactional store for workflow, search engine
  for analytics. The thesis can argue this in Chapter 3 (Design) with
  references rather than presenting it as a novel choice.
- ✅ Search performance is no longer bounded by Postgres. ES returns
  filtered + sorted hits over the events index in low single-digit
  milliseconds for the prototype's data volumes.
- ✅ The dual-write happens in one place (`EventWriter.insert_event`)
  and is gated by the same idempotency check as the Postgres write,
  so duplicates do not double-index.
- ⚠️ Two stores mean two failure modes. The Normalizer logs ES errors
  and keeps going; an operator must monitor the Elasticsearch health
  separately. Acceptable for a prototype; production would add a
  reconciliation job that replays missing event_ids from Postgres.
- ⚠️ Daily indices created lazily on the first event of the day. No
  ILM (Index Lifecycle Management) policy is configured — old
  indices live forever until someone deletes them. Documented as
  Future work; the prototype's data volume does not justify the
  added complexity.
- ⚠️ Security disabled (`xpack.security.enabled=false`). Acceptable
  for a localhost demo; a production deployment must enable TLS,
  basic auth, and role-based index access.

---



## Future decisions (placeholder)

Records added during weeks 2–10 will appear below as the system grows:
correlation rule design, alert deduplication strategy, frontend technology,
testing approach, etc.