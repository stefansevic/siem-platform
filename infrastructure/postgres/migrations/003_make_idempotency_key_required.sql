-- ============================================
-- Migration 003: Make idempotency_key required and unique
-- ============================================
-- The Normalizer always generates an idempotency_key (deterministic
-- SHA-256 of source/format/payload), so the column will never be NULL
-- in normal operation. Replacing the partial UNIQUE index with a regular
-- UNIQUE constraint enables ON CONFLICT (idempotency_key) DO NOTHING
-- to work directly without index_predicate gymnastics.
--
-- Order matters:
--   1. Backfill any pre-existing NULL rows (none expected, but defensive).
--   2. Drop the partial unique index from migration 002.
--   3. Add NOT NULL + UNIQUE constraint.

-- Step 1: backfill (uses row id as a unique placeholder, satisfies UNIQUE)
UPDATE events
SET idempotency_key = 'backfill-' || id::text
WHERE idempotency_key IS NULL;

-- Step 2: drop the partial index from migration 002
DROP INDEX IF EXISTS uq_events_idempotency_key;

-- Step 3: tighten the column
ALTER TABLE events
    ALTER COLUMN idempotency_key SET NOT NULL,
    ADD CONSTRAINT uq_events_idempotency_key UNIQUE (idempotency_key);
