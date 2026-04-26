-- ============================================
-- SIEM Platform - Database Schema
-- ============================================
-- Initialized automatically on first Postgres container start.
-- Drop and recreate volume to re-run: `docker compose down -v`

-- ============================================
-- Extensions
-- ============================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- events: normalized log events (ECS-compatible)
-- ============================================
-- Stores every log event after the Normalizer maps it to ECS.
-- Serves as audit trail and as the data source for dashboard queries.

CREATE TABLE IF NOT EXISTS events (
    id                       UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- ECS core fields
    timestamp                TIMESTAMPTZ  NOT NULL,
    event_category           VARCHAR(64)  NOT NULL,
    event_outcome            VARCHAR(32),
    event_action             VARCHAR(64),

    -- Network / source fields
    source_ip                INET,
    source_port              INTEGER,

    -- User identity
    user_name                VARCHAR(255),

    -- HTTP-specific fields
    http_method              VARCHAR(16),
    url_path                 TEXT,
    http_response_status_code INTEGER,
    user_agent               TEXT,

    -- Provenance: which service produced/normalized this event
    log_source               VARCHAR(64)  NOT NULL,

    -- Full original message for debugging / forensics
    raw_message              TEXT,

    -- Bookkeeping
    received_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_events_timestamp
    ON events (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_events_source_ip_timestamp
    ON events (source_ip, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_events_category_outcome
    ON events (event_category, event_outcome);

CREATE INDEX IF NOT EXISTS idx_events_status_code
    ON events (http_response_status_code);

CREATE INDEX IF NOT EXISTS idx_events_user_name
    ON events (user_name)
    WHERE user_name IS NOT NULL;

-- ============================================
-- incidents: generated alerts from correlation rules
-- ============================================

CREATE TABLE IF NOT EXISTS incidents (
    id                  UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- What was detected
    rule_name           VARCHAR(128)  NOT NULL,
    rule_version        VARCHAR(32),
    severity            VARCHAR(16)   NOT NULL CHECK (severity IN ('low','medium','high','critical')),

    -- When the attack pattern was observed
    first_event_at      TIMESTAMPTZ   NOT NULL,
    last_event_at       TIMESTAMPTZ   NOT NULL,
    detected_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    -- Who/what was involved
    source_ip           INET,
    target_user_name    VARCHAR(255),

    -- Aggregated count of events that triggered the rule
    event_count         INTEGER       NOT NULL,

    -- Free-form context (rule parameters, sample events, etc.)
    details             JSONB,

    -- Optional list of event IDs that contributed to this incident
    contributing_events UUID[],

    -- Workflow status
    status              VARCHAR(32)   NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','acknowledged','closed','false_positive')),
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_detected_at
    ON incidents (detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_incidents_rule_name
    ON incidents (rule_name);

CREATE INDEX IF NOT EXISTS idx_incidents_source_ip
    ON incidents (source_ip);

CREATE INDEX IF NOT EXISTS idx_incidents_status
    ON incidents (status);

-- ============================================
-- Sanity check: confirm tables exist
-- ============================================
DO $$
BEGIN
    RAISE NOTICE 'SIEM schema initialized: events, incidents';
END $$;