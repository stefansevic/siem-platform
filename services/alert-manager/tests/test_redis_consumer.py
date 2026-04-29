"""
Integration tests for AlertManagerConsumer against live Redis + Postgres.

End-to-end coverage:
    - Single trigger inserts a fresh incident
    - Multiple triggers within silence window collapse into one
    - Triggers past the silence window create separate incidents
    - Notifier is called for both INSERT and UPDATE actions
    - Malformed entries are acked silently
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import uuid4

import pytest
import pytest_asyncio
from redis import asyncio as aioredis
from sqlalchemy import text

from shared.ecs_models import Incident, IncidentSeverity, IncidentStatus
from shared.redis_keys import GROUP_ALERT_MANAGER, STREAM_INCIDENTS

from app.db import IncidentStore, build_dsn
from app.notifiers import Notifier
from app.redis_consumer import AlertManagerConsumer

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
    await client.delete(STREAM_INCIDENTS)
    yield client
    await client.delete(STREAM_INCIDENTS)
    await client.aclose()


@pytest_asyncio.fixture
async def store():
    s = IncidentStore(_pg_dsn())
    await s.connect()

    async with s._engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(text(
            "DELETE FROM incidents WHERE rule_name LIKE 'amgr-test-%'"
        ))

    yield s

    async with s._engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(text(
            "DELETE FROM incidents WHERE rule_name LIKE 'amgr-test-%'"
        ))
    await s.close()


class _RecordingNotifier(Notifier):
    """Captures every notify() call in order."""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    async def notify(self, incident: Incident, *, was_merged: bool) -> None:
        self.calls.append({
            "id": incident.id,
            "rule_name": incident.rule_name,
            "event_count": incident.event_count,
            "was_merged": was_merged,
        })


@pytest_asyncio.fixture
async def consumer(redis_client, store):
    notifier = _RecordingNotifier()
    c = AlertManagerConsumer(
        redis_url=_redis_url(),
        store=store,
        notifier=notifier,
        silence_window=timedelta(minutes=5),
        consumer_name="test-am",
    )
    await c.connect()
    # Attach notifier to consumer so tests can inspect it
    c._test_notifier = notifier  # type: ignore[attr-defined]
    yield c
    await c.close()


# ============================================
# Helpers
# ============================================

T0 = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_trigger(
    *,
    rule_name: str = "amgr-test-brute",
    source_ip: str = "1.2.3.4",
    last_event_at=None,
    event_count: int = 5,
) -> Incident:
    return Incident(
        id=uuid4(),
        rule_name=rule_name,
        rule_version="1.0",
        severity=IncidentSeverity.HIGH,
        first_event_at=at(0),
        last_event_at=last_event_at or at(0),
        source_ip=source_ip,
        event_count=event_count,
        contributing_events=[uuid4() for _ in range(event_count)],
        status=IncidentStatus.OPEN,
    )


async def push(redis_client, incident: Incident) -> str:
    body = incident.model_dump(mode="json")
    return await redis_client.xadd(
        STREAM_INCIDENTS, {"data": json.dumps(body, separators=(",", ":"))},
    )


async def drain(consumer: AlertManagerConsumer) -> None:
    entries = await consumer._read_batch()
    if entries:
        for _, messages in entries:
            for entry_id, fields in messages:
                await consumer._process_entry(entry_id, fields)


async def count_test_rows(store: IncidentStore) -> int:
    async with store._engine.begin() as conn:  # type: ignore[attr-defined]
        row = (await conn.execute(text(
            "SELECT COUNT(*) AS n FROM incidents WHERE rule_name LIKE 'amgr-test-%'"
        ))).one()
    return row.n


# ============================================
# Tests
# ============================================

class TestPipeline:
    async def test_single_trigger_inserts_one_incident(
        self, redis_client, consumer, store,
    ):
        await push(redis_client, make_trigger())
        await drain(consumer)

        assert await count_test_rows(store) == 1
        assert len(consumer._test_notifier.calls) == 1
        assert consumer._test_notifier.calls[0]["was_merged"] is False

    async def test_two_triggers_in_window_become_one_incident(
        self, redis_client, consumer, store,
    ):
        # First trigger: count=5
        await push(redis_client, make_trigger(
            event_count=5, last_event_at=at(0),
        ))
        # Second trigger: count=6, 60s later (well within 5-min silence)
        await push(redis_client, make_trigger(
            event_count=6, last_event_at=at(60),
        ))
        await drain(consumer)

        # ONLY ONE row in DB despite two stream entries
        assert await count_test_rows(store) == 1

        # And both notifications fired (insert + merge)
        calls = consumer._test_notifier.calls
        assert len(calls) == 2
        assert calls[0]["was_merged"] is False
        assert calls[1]["was_merged"] is True
        # Merged incident reflects the higher count
        assert calls[1]["event_count"] == 6

    async def test_trigger_past_silence_window_creates_new_incident(
        self, redis_client, consumer, store,
    ):
        # First trigger
        await push(redis_client, make_trigger(last_event_at=at(0)))
        # Second trigger 10 minutes later — outside 5-min window
        await push(redis_client, make_trigger(last_event_at=at(600)))
        await drain(consumer)

        # Two separate incidents
        assert await count_test_rows(store) == 2

        # Both treated as inserts (not merges)
        calls = consumer._test_notifier.calls
        assert len(calls) == 2
        assert all(c["was_merged"] is False for c in calls)

    async def test_different_ips_get_isolated_incidents(
        self, redis_client, consumer, store,
    ):
        await push(redis_client, make_trigger(source_ip="1.1.1.1"))
        await push(redis_client, make_trigger(source_ip="2.2.2.2"))
        await drain(consumer)

        # Two separate incidents (different attackers)
        assert await count_test_rows(store) == 2

    async def test_dedup_persists_correct_event_count(
        self, redis_client, consumer, store,
    ):
        # Three triggers escalating: count 5, 7, 10
        for cnt, ts in [(5, at(0)), (7, at(30)), (10, at(60))]:
            await push(redis_client, make_trigger(
                event_count=cnt, last_event_at=ts,
            ))
        await drain(consumer)

        # Single row in DB with the maximum count
        async with store._engine.begin() as conn:  # type: ignore[attr-defined]
            row = (await conn.execute(text(
                "SELECT event_count, last_event_at FROM incidents "
                "WHERE rule_name = 'amgr-test-brute'"
            ))).one()
        assert row.event_count == 10
        assert row.last_event_at == at(60)


class TestRobustness:
    async def test_malformed_json_acked_silently(
        self, redis_client, consumer, store,
    ):
        await redis_client.xadd(
            STREAM_INCIDENTS, {"data": "not valid json {{{"},
        )
        await drain(consumer)

        # Nothing in DB
        assert await count_test_rows(store) == 0

        # Entry was acked despite being garbage
        pending = await redis_client.xpending(
            STREAM_INCIDENTS, GROUP_ALERT_MANAGER,
        )
        assert pending["pending"] == 0