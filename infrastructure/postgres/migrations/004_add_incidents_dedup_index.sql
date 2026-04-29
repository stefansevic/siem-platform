-- ============================================
-- Migration 004: Index for incident deduplication lookups
-- ============================================
-- The Alert Manager deduplicates triggers from the Correlator by
-- looking up an existing OPEN incident for (rule_name, source_ip)
-- whose last_event_at is within a configurable silence window
-- (default 5 minutes).
--
-- Without an index, that lookup is a full sequential scan over all
-- historical incidents — fine at 50 rows, painful at 50,000.
--
-- A partial composite index keyed on (rule_name, source_ip) and
-- ordered by last_event_at DESC gives O(log n) lookups for the
-- "most recent open incident matching this trigger" query.
--
-- WHERE status = 'open' shrinks the index to only currently active
-- incidents, which is the only state the Alert Manager queries against.

CREATE INDEX IF NOT EXISTS idx_incidents_dedup
    ON incidents (rule_name, source_ip, last_event_at DESC)
    WHERE status = 'open';