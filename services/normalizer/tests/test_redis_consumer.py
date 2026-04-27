"""
Integration tests for NormalizerConsumer against live Redis + Postgres.

These tests drive one full pipeline iteration end-to-end:
    Ingestor-style envelope -> Redis raw_logs -> consumer reads, normalizes,
    persists, republishes to normalized_events (or dead-letters).

Each test isolates state by:
    * Deleting the raw_logs / normalized_events / dead_letter_logs streams
    * Wiping events rows whose idempotency_key starts with 'consumer-test-'
"""

from __future__ import annotations

import json
import os
from typing import Optional

import pytest
import pytest_asyncio
from redis import asyncio as aioredis
from sqlalchemy import text

from shared.redis_keys import (
    GROUP_NORMALIZER,
    STREAM_DEAD_LETTER,
    STREAM_NORMALIZED_EVENTS,
    STREAM_RAW_LOGS,
)

from app.db import EventWriter, build_dsn
from app.redis_consumer import NormalizerConsumer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ============================================
# Fixtures
# ============================================

def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST_TEST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    return f"redis://{host}:{port}/0"


def _pg_dsn() -> str:
    return build_dsn(host=os.environ.get("POSTGRES_HOST_TEST", "localhost"))


@pytest_asyncio.fixture
async def redis_client():
    client = aioredis.from_url(_redis_url(), decode_responses=True)
    await client.ping()

    # Clean slate for the streams used in the test
    for stream in (STREAM_RAW_LOGS, STREAM_NORMALIZED_EVENTS, STREAM_DEAD_LETTER):
        await client.delete(stream)

    yield client

    for stream in (STREAM_RAW_LOGS, STREAM_NORMALIZED_EVENTS, STREAM_DEAD_LETTER):
        await client.delete(stream)
    await client.aclose()


@pytest_asyncio.fixture
async def writer():
    w = EventWriter(_pg_dsn())
    await w.connect()

    async with w._engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(text(
            "DELETE FROM events WHERE idempotency_key LIKE 'consumer-test-%' "
            "OR raw_message LIKE '%consumer-test-marker%'"
        ))

    yield w

    async with w._engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(text(
            "DELETE FROM events WHERE idempotency_key LIKE 'consumer-test-%' "
            "OR raw_message LIKE '%consumer-test-marker%'"
        ))
    await w.close()


@pytest_asyncio.fixture
async def consumer(redis_client, writer):
    c = NormalizerConsumer(
        redis_url=_redis_url(),
        writer=writer,
        consumer_name="test-consumer",
    )
    await c.connect()
    yield c
    await c.close()


# ============================================
# Helpers
# ============================================

# Marker string in payload so tests can locate their own rows for cleanup.
_MARKER = "consumer-test-marker"

WEBAPP_AUTH_FAILURE_PAYLOAD = json.dumps({
    "timestamp": "2026-04-27T10:05:27.123456+00:00",
    "level": "WARNING",
    "logger": "demo-webapp",
    "message": "authentication_failure",
    "event_type": "authentication",
    "outcome": "failure",
    "username": "admin",
    "source_ip": "172.18.0.1",
    "marker": _MARKER,
})

WEBAPP_LIFECYCLE_PAYLOAD = json.dumps({
    "timestamp": "2026-04-27T10:00:00+00:00",
    "level": "INFO",
    "logger": "demo-webapp",
    "message": "demo_webapp_started",
    "event_type": "lifecycle",
    "marker": _MARKER,
})


async def _push_envelope(redis_client, source: str, fmt: str, payload: str) -> str:
    """Mimic what the Ingestor publishes onto raw_logs."""
    envelope = {
        "message_id": "00000000-0000-0000-0000-000000000001",
        "received_at": "2026-04-27T10:05:27Z",
        "source": source,
        "format": fmt,
        "payload": payload,
        "origin": "test",
    }
    return await redis_client.xadd(
        STREAM_RAW_LOGS, {"data": json.dumps(envelope)}
    )


async def _drain_one(consumer: NormalizerConsumer) -> None:
    """Run the consumer for exactly one batch then stop."""
    entries = await consumer._read_batch()
    if entries:
        for _, messages in entries:
            for entry_id, fields in messages:
                await consumer._process_entry(entry_id, fields)


# ============================================
# Tests
# ============================================

class TestPipeline:
    async def test_auth_failure_persists_and_republishes(
        self, redis_client, consumer, writer,
    ):
        await _push_envelope(
            redis_client, "demo-webapp", "json", WEBAPP_AUTH_FAILURE_PAYLOAD
        )
        await _drain_one(consumer)

        # 1. Row landed in Postgres
        async with writer._engine.begin() as conn:  # type: ignore[attr-defined]
            row = (await conn.execute(text(
                "SELECT user_name, event_category, event_outcome "
                "FROM events WHERE raw_message LIKE :m"
            ), {"m": f"%{_MARKER}%"})).one_or_none()
        assert row is not None, "event was not persisted"
        assert row.user_name == "admin"
        assert row.event_category == "authentication"
        assert row.event_outcome == "failure"

        # 2. Republished to normalized_events
        normalized = await redis_client.xrange(STREAM_NORMALIZED_EVENTS)
        assert len(normalized) == 1
        body = json.loads(normalized[0][1]["data"])
        assert body["user_name"] == "admin"
        assert body["event_outcome"] == "failure"

        # 3. Original entry was acked (no PEL)
        pending = await redis_client.xpending(STREAM_RAW_LOGS, GROUP_NORMALIZER)
        assert pending["pending"] == 0

    async def test_duplicate_payload_is_idempotent(
        self, redis_client, consumer, writer,
    ):
        # Same payload twice -> the Ingestor would assign different message_ids,
        # but compute_idempotency_key only hashes (source, format, payload).
        await _push_envelope(
            redis_client, "demo-webapp", "json", WEBAPP_AUTH_FAILURE_PAYLOAD
        )
        await _push_envelope(
            redis_client, "demo-webapp", "json", WEBAPP_AUTH_FAILURE_PAYLOAD
        )
        await _drain_one(consumer)

        async with writer._engine.begin() as conn:  # type: ignore[attr-defined]
            count = (await conn.execute(text(
                "SELECT COUNT(*) AS n FROM events WHERE raw_message LIKE :m"
            ), {"m": f"%{_MARKER}%"})).one().n
        assert count == 1, "duplicate insert leaked through"

        # Both originals acked
        pending = await redis_client.xpending(STREAM_RAW_LOGS, GROUP_NORMALIZER)
        assert pending["pending"] == 0

    async def test_lifecycle_event_is_skipped(
        self, redis_client, consumer, writer,
    ):
        await _push_envelope(
            redis_client, "demo-webapp", "json", WEBAPP_LIFECYCLE_PAYLOAD
        )
        await _drain_one(consumer)

        async with writer._engine.begin() as conn:  # type: ignore[attr-defined]
            count = (await conn.execute(text(
                "SELECT COUNT(*) AS n FROM events WHERE raw_message LIKE :m"
            ), {"m": f"%{_MARKER}%"})).one().n
        assert count == 0, "lifecycle event should not be persisted"

        # Not republished either
        normalized = await redis_client.xrange(STREAM_NORMALIZED_EVENTS)
        assert len(normalized) == 0

        # Still acked: not a failure
        pending = await redis_client.xpending(STREAM_RAW_LOGS, GROUP_NORMALIZER)
        assert pending["pending"] == 0


class TestDeadLetter:
    async def test_malformed_json_payload_goes_to_dead_letter(
        self, redis_client, consumer, writer,
    ):
        await _push_envelope(
            redis_client, "demo-webapp", "json",
            "not valid json {{{ consumer-test-marker"
        )
        await _drain_one(consumer)

        # Nothing in events
        async with writer._engine.begin() as conn:  # type: ignore[attr-defined]
            count = (await conn.execute(text(
                "SELECT COUNT(*) AS n FROM events WHERE raw_message LIKE :m"
            ), {"m": f"%{_MARKER}%"})).one().n
        assert count == 0

        # One entry on dead_letter_logs
        dead = await redis_client.xrange(STREAM_DEAD_LETTER)
        assert len(dead) == 1
        assert "normalize:" in dead[0][1]["reason"]

        # And the original entry was acked
        pending = await redis_client.xpending(STREAM_RAW_LOGS, GROUP_NORMALIZER)
        assert pending["pending"] == 0

    async def test_invalid_envelope_goes_to_dead_letter(
        self, redis_client, consumer,
    ):
        # The "data" field is not valid JSON at all
        await redis_client.xadd(
            STREAM_RAW_LOGS, {"data": "this is not an envelope"}
        )
        await _drain_one(consumer)

        dead = await redis_client.xrange(STREAM_DEAD_LETTER)
        assert len(dead) == 1
        assert "envelope:" in dead[0][1]["reason"]

        pending = await redis_client.xpending(STREAM_RAW_LOGS, GROUP_NORMALIZER)
        assert pending["pending"] == 0