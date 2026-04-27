-- ============================================
-- Migration 002: Add idempotency_key to events
-- ============================================
-- The Normalizer is an at-least-once consumer of the raw_logs Redis stream.
-- A network blip between processing the event and ack-ing the message means
-- the same payload may be redelivered after restart.
--
-- To make ingestion exactly-once at the storage layer, every ECSEvent
-- carries a deterministic key derived from the raw payload (SHA-256 hex).
-- Inserts use ON CONFLICT (idempotency_key) DO NOTHING so duplicates
-- become silent no-ops without disturbing correlation counters.
--
-- Notes:
--   - 64 chars = SHA-256 hex digest length.
--   - NULL is permitted for backward compatibility with rows already in the
--     table (none in our case, but cheap insurance).
--   - The UNIQUE constraint is created via a partial index so that NULL keys
--     are not considered duplicates of one another (Postgres-default behavior
--     would already do this with a plain UNIQUE constraint, but a partial
--     index makes the intent explicit and skips index entries for NULL rows).

ALTER TABLE events
    ADD COLUMN IF NOT EXISTS idempotency_key CHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_events_idempotency_key
    ON events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;