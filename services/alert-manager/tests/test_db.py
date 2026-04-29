"""
Integration tests for IncidentStore against a live Postgres container.

Tests cover the two operations Alert Manager performs:
    * find_open_match — used before dedup decision
    * insert / update  — used after dedup decision

Each test isolates state by wiping incidents whose rule_name starts
with 'amgr-test-' so concurrent or repeated runs do not interfere.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from shared.ecs_models import Incident, IncidentSeverity, IncidentStatus

from app.db import IncidentStore, build_dsn

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ============================================
# Fixtures
# ============================================

@pytest_asyncio.fixture
async def store():
    dsn = build_dsn(host=os.environ.get("POSTGRES_HOST_TEST", "localhost"))
    s = IncidentStore(dsn)
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


# ============================================
# Helpers
# ============================================

T0 = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_incident(
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


# ============================================
# find_open_match
# ============================================

class TestFindOpenMatch:
    async def test_no_match_returns_none(self, store: IncidentStore):
        result = await store.find_open_match("amgr-test-nothing", "9.9.9.9")
        assert result is None

    async def test_finds_existing_open_incident(self, store: IncidentStore):
        original = make_incident()
        await store.insert(original)

        found = await store.find_open_match(
            original.rule_name, str(original.source_ip),
        )
        assert found is not None
        assert found.id == original.id
        assert found.event_count == original.event_count

    async def test_does_not_find_closed_incident(self, store: IncidentStore):
        """Closed incidents should never absorb new triggers."""
        # Insert an incident, then mark it closed via direct SQL
        incident = make_incident()
        await store.insert(incident)

        async with store._engine.begin() as conn:  # type: ignore[attr-defined]
            await conn.execute(
                text("UPDATE incidents SET status = 'closed' WHERE id = :id"),
                {"id": incident.id},
            )

        result = await store.find_open_match(
            incident.rule_name, str(incident.source_ip),
        )
        assert result is None

    async def test_returns_most_recent_when_multiple_open(
        self, store: IncidentStore,
    ):
        """If two open incidents match (rare in practice, but possible),
        return the one with the most recent last_event_at."""
        older = make_incident(last_event_at=at(0))
        newer = make_incident(last_event_at=at(60))
        await store.insert(older)
        await store.insert(newer)

        found = await store.find_open_match(
            "amgr-test-brute", "1.2.3.4",
        )
        assert found is not None
        assert found.id == newer.id

    async def test_isolates_by_rule_name(self, store: IncidentStore):
        a = make_incident(rule_name="amgr-test-rule-a")
        b = make_incident(rule_name="amgr-test-rule-b")
        await store.insert(a)
        await store.insert(b)

        found_a = await store.find_open_match("amgr-test-rule-a", "1.2.3.4")
        found_b = await store.find_open_match("amgr-test-rule-b", "1.2.3.4")
        assert found_a.id == a.id
        assert found_b.id == b.id

    async def test_isolates_by_source_ip(self, store: IncidentStore):
        a = make_incident(source_ip="10.0.0.1")
        b = make_incident(source_ip="10.0.0.2")
        await store.insert(a)
        await store.insert(b)

        found_a = await store.find_open_match("amgr-test-brute", "10.0.0.1")
        found_b = await store.find_open_match("amgr-test-brute", "10.0.0.2")
        assert found_a.id == a.id
        assert found_b.id == b.id


# ============================================
# insert
# ============================================

class TestInsert:
    async def test_insert_persists_all_fields(self, store: IncidentStore):
        incident = make_incident(event_count=7)
        await store.insert(incident)

        async with store._engine.begin() as conn:  # type: ignore[attr-defined]
            row = (await conn.execute(text(
                "SELECT rule_name, severity, event_count, "
                "       host(source_ip) AS source_ip, "
                "       array_length(contributing_events, 1) AS contrib_count "
                "FROM incidents WHERE id = :id"
            ), {"id": incident.id})).one()

        assert row.rule_name == incident.rule_name
        assert row.severity == "high"
        assert row.event_count == 7
        assert row.source_ip == "1.2.3.4"
        assert row.contrib_count == 7  # one UUID per event in our helper


# ============================================
# update
# ============================================

class TestUpdate:
    async def test_update_modifies_event_count_and_last_event_at(
        self, store: IncidentStore,
    ):
        incident = make_incident(event_count=5, last_event_at=at(0))
        await store.insert(incident)

        merged = incident.model_copy(update={
            "event_count": 8,
            "last_event_at": at(60),
            "details": {"merged": True},
        })
        await store.update(merged)

        async with store._engine.begin() as conn:  # type: ignore[attr-defined]
            row = (await conn.execute(text(
                "SELECT event_count, last_event_at, details "
                "FROM incidents WHERE id = :id"
            ), {"id": incident.id})).one()

        assert row.event_count == 8
        assert row.last_event_at == at(60)
        assert row.details == {"merged": True}

    async def test_update_preserves_first_event_at_and_detected_at(
        self, store: IncidentStore,
    ):
        original = make_incident(event_count=5)
        await store.insert(original)

        # Pretend a merge happens far in the future
        merged = original.model_copy(update={
            "first_event_at": at(99999),  # tries to overwrite — must be ignored
            "event_count": 10,
            "last_event_at": at(60),
        })
        await store.update(merged)

        async with store._engine.begin() as conn:  # type: ignore[attr-defined]
            row = (await conn.execute(text(
                "SELECT first_event_at, detected_at "
                "FROM incidents WHERE id = :id"
            ), {"id": original.id})).one()

        # update() does NOT touch first_event_at or detected_at
        assert row.first_event_at == original.first_event_at
        assert row.detected_at == original.detected_at

    async def test_update_does_not_touch_closed_incident(
        self, store: IncidentStore,
    ):
        """If someone closed the incident between find and update,
        the update is a no-op (WHERE status='open' filters it out)."""
        incident = make_incident(event_count=5)
        await store.insert(incident)

        async with store._engine.begin() as conn:  # type: ignore[attr-defined]
            await conn.execute(
                text("UPDATE incidents SET status = 'closed' WHERE id = :id"),
                {"id": incident.id},
            )

        merged = incident.model_copy(update={"event_count": 999})
        await store.update(merged)  # no-op

        async with store._engine.begin() as conn:  # type: ignore[attr-defined]
            row = (await conn.execute(text(
                "SELECT event_count FROM incidents WHERE id = :id"
            ), {"id": incident.id})).one()

        assert row.event_count == 5  # unchanged