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


## Future decisions (placeholder)

Records added during weeks 2–10 will appear below as the system grows:
correlation rule design, alert deduplication strategy, frontend technology,
testing approach, etc.