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

## Future decisions (placeholder)

Records added during weeks 2–10 will appear below as the system grows:
correlation rule design, alert deduplication strategy, frontend technology,
testing approach, etc.