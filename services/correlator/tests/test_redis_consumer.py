"""
Integration tests for CorrelatorConsumer against live Redis.

Each test:
    1. Wipes normalized_events and incidents streams.
    2. Pushes synthetic ECSEvents (as JSON) to normalized_events.
    3. Drains one consumer batch.
    4. Asserts incidents stream content + ack state.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from redis import asyncio as aioredis

from shared.ecs_models import (
    ECSEvent,
    EventCategory,
    EventOutcome,
    LogSource,
)
from shared.redis_keys import (
    GROUP_CORRELATOR,
    STREAM_INCIDENTS,
    STREAM_NORMALIZED_EVENTS,
)

from app.engine import CorrelationEngine
from app.redis_consumer import CorrelatorConsumer
from app.rules import (
    AccountTakeoverRule,
    BruteForceRule,
    DirectoryScanningRule,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ============================================
# Fixtures
# ============================================

def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST_TEST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    return f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def redis_client():
    client = aioredis.from_url(_redis_url(), decode_responses=True)
    await client.ping()
    for stream in (STREAM_NORMALIZED_EVENTS, STREAM_INCIDENTS):
        await client.delete(stream)
    yield client
    for stream in (STREAM_NORMALIZED_EVENTS, STREAM_INCIDENTS):
        await client.delete(stream)
    await client.aclose()


@pytest_asyncio.fixture
async def consumer(redis_client):
    engine = CorrelationEngine([
        BruteForceRule(threshold=3, window=timedelta(minutes=1)),
        DirectoryScanningRule(threshold=5, window=timedelta(seconds=30)),
        AccountTakeoverRule(failure_threshold=2, window=timedelta(minutes=5)),
    ])
    c = CorrelatorConsumer(
        redis_url=_redis_url(),
        engine=engine,
        consumer_name="test-correlator",
    )
    await c.connect()
    yield c
    await c.close()


# ============================================
# Helpers
# ============================================

T0 = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


async def push_event(redis_client, event: ECSEvent) -> str:
    """Mimic what the Normalizer publishes to normalized_events."""
    body = event.model_dump(mode="json")
    return await redis_client.xadd(
        STREAM_NORMALIZED_EVENTS,
        {"data": json.dumps(body, separators=(",", ":"))},
    )


def auth_failure(ip: str = "1.2.3.4", user: str = "alice", ts=None) -> ECSEvent:
    return ECSEvent(
        timestamp=ts or at(0),
        event_category=EventCategory.AUTHENTICATION,
        event_outcome=EventOutcome.FAILURE,
        source_ip=ip,
        user_name=user,
        log_source=LogSource.DEMO_WEBAPP,
    )


def auth_success(ip: str = "1.2.3.4", user: str = "admin", ts=None) -> ECSEvent:
    return ECSEvent(
        timestamp=ts or at(0),
        event_category=EventCategory.AUTHENTICATION,
        event_outcome=EventOutcome.SUCCESS,
        source_ip=ip,
        user_name=user,
        log_source=LogSource.DEMO_WEBAPP,
    )


def http_404(ip: str = "1.2.3.4", path: str = "/x", ts=None) -> ECSEvent:
    return ECSEvent(
        timestamp=ts or at(0),
        event_category=EventCategory.WEB,
        event_outcome=EventOutcome.FAILURE,
        source_ip=ip,
        http_response_status_code=404,
        url_path=path,
        log_source=LogSource.NGINX,
    )


async def drain_one(consumer: CorrelatorConsumer) -> None:
    entries = await consumer._read_batch()
    if entries:
        for _, messages in entries:
            for entry_id, fields in messages:
                await consumer._process_entry(entry_id, fields)


# ============================================
# Tests
# ============================================

class TestPipeline:
    async def test_brute_force_chain_emits_incident(self, redis_client, consumer):
        # Threshold = 3. Push 3 failures from one IP.
        for i in range(3):
            await push_event(redis_client, auth_failure(ts=at(i)))
        await drain_one(consumer)

        incidents = await redis_client.xrange(STREAM_INCIDENTS)
        assert len(incidents) == 1
        body = json.loads(incidents[0][1]["data"])
        assert body["rule_name"] == "brute_force"
        assert body["source_ip"] == "1.2.3.4"
        assert body["event_count"] == 3

        # All 3 source events acked
        pending = await redis_client.xpending(
            STREAM_NORMALIZED_EVENTS, GROUP_CORRELATOR,
        )
        assert pending["pending"] == 0

    async def test_below_threshold_emits_nothing(self, redis_client, consumer):
        for i in range(2):  # 2 failures, threshold is 3
            await push_event(redis_client, auth_failure(ts=at(i)))
        await drain_one(consumer)

        incidents = await redis_client.xrange(STREAM_INCIDENTS)
        assert incidents == []

    async def test_directory_scan_emits_incident(self, redis_client, consumer):
        # Threshold = 5 distinct paths
        for i in range(5):
            await push_event(redis_client, http_404(path=f"/p-{i}", ts=at(i)))
        await drain_one(consumer)

        incidents = await redis_client.xrange(STREAM_INCIDENTS)
        assert len(incidents) == 1
        body = json.loads(incidents[0][1]["data"])
        assert body["rule_name"] == "directory_scanning"
        assert body["details"]["distinct_paths_count"] == 5

    async def test_account_takeover_emits_on_success(
        self, redis_client, consumer,
    ):
        # 2 failures + 1 success (failure_threshold = 2)
        await push_event(redis_client, auth_failure(user="alice", ts=at(0)))
        await push_event(redis_client, auth_failure(user="bob", ts=at(1)))
        await push_event(redis_client, auth_success(user="admin", ts=at(2)))
        await drain_one(consumer)

        incidents = await redis_client.xrange(STREAM_INCIDENTS)
        # Could be 1 (ATO) or 2 (brute_force + ATO) depending on whether
        # brute-force fires on the second failure with threshold=3.
        # With threshold=3, brute-force does NOT fire (only 2 failures).
        # Only ATO fires on the success.
        assert len(incidents) == 1
        body = json.loads(incidents[0][1]["data"])
        assert body["rule_name"] == "account_takeover"
        assert body["details"]["successful_user_name"] == "admin"

    async def test_two_attackers_isolated(self, redis_client, consumer):
        # IP A reaches threshold, IP B does not
        for i in range(3):
            await push_event(redis_client, auth_failure(ip="1.1.1.1", ts=at(i)))
        for i in range(2):
            await push_event(redis_client, auth_failure(ip="2.2.2.2", ts=at(10 + i)))
        await drain_one(consumer)

        incidents = await redis_client.xrange(STREAM_INCIDENTS)
        assert len(incidents) == 1
        body = json.loads(incidents[0][1]["data"])
        assert body["source_ip"] == "1.1.1.1"


class TestRobustness:
    async def test_malformed_json_is_acked_silently(
        self, redis_client, consumer,
    ):
        # Push raw garbage that cannot deserialize
        await redis_client.xadd(
            STREAM_NORMALIZED_EVENTS, {"data": "not valid json {{{"},
        )
        await drain_one(consumer)

        # No incident, but the entry must be acked so the loop moves on
        incidents = await redis_client.xrange(STREAM_INCIDENTS)
        assert incidents == []

        pending = await redis_client.xpending(
            STREAM_NORMALIZED_EVENTS, GROUP_CORRELATOR,
        )
        assert pending["pending"] == 0