"""
Integration tests for db.EventWriter against a live Postgres container.

These tests are slower than the pure-function tests and require:
    - Postgres reachable via POSTGRES_* environment variables
    - The schema from migrations/001 + 002 already applied

To run only fast unit tests:    pytest -m "not integration"
To run only DB integration:     pytest -m integration
To run everything:              pytest
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from shared.ecs_models import ECSEvent, EventCategory, EventOutcome, LogSource

from app.db import EventWriter, build_dsn

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ============================================
# Fixtures
# ============================================

@pytest_asyncio.fixture
async def writer():
    """
    Yield a connected EventWriter, clean up the events table afterwards
    so tests stay isolated.
    """
    dsn = build_dsn(host=os.environ.get("POSTGRES_HOST_TEST", "localhost"))
    w = EventWriter(dsn)
    await w.connect()

    # Wipe events written by previous test runs that share idempotency_key
    async with w._engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(text(
            "DELETE FROM events WHERE idempotency_key LIKE 'test-%'"
        ))

    yield w

    async with w._engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(text(
            "DELETE FROM events WHERE idempotency_key LIKE 'test-%'"
        ))
    await w.close()


def _sample_event(**overrides) -> ECSEvent:
    base = dict(
        timestamp=datetime(2026, 4, 27, 10, 0, 0, tzinfo=timezone.utc),
        event_category=EventCategory.AUTHENTICATION,
        event_outcome=EventOutcome.FAILURE,
        source_ip="172.18.0.1",
        user_name="admin",
        log_source=LogSource.DEMO_WEBAPP,
        raw_message='{"event_type":"authentication","outcome":"failure"}',
    )
    base.update(overrides)
    return ECSEvent(**base)


# ============================================
# Tests
# ============================================

class TestEventWriter:
    async def test_insert_returns_true_on_new_row(self, writer: EventWriter):
        event = _sample_event()
        inserted = await writer.insert_event(event, idempotency_key="test-001")
        assert inserted is True

    async def test_duplicate_insert_returns_false(self, writer: EventWriter):
        # Two events with different IDs but the SAME idempotency_key.
        # The second insert must collapse into a no-op.
        event_a = _sample_event(id=uuid4())
        event_b = _sample_event(id=uuid4())

        first = await writer.insert_event(event_a, idempotency_key="test-002")
        second = await writer.insert_event(event_b, idempotency_key="test-002")

        assert first is True
        assert second is False

    async def test_inserted_row_has_correct_fields(self, writer: EventWriter):
        event = _sample_event()
        await writer.insert_event(event, idempotency_key="test-003")

        async with writer._engine.begin() as conn:  # type: ignore[attr-defined]
            row = (await conn.execute(text(
                "SELECT user_name, event_category, event_outcome, "
                "       host(source_ip) AS source_ip "
                "FROM events WHERE idempotency_key = 'test-003'"
            ))).one()

        assert row.user_name == "admin"
        assert row.event_category == "authentication"
        assert row.event_outcome == "failure"
        assert row.source_ip == "172.18.0.1"

    async def test_event_with_no_source_ip(self, writer: EventWriter):
        """Lifecycle-style events have no source_ip; insert must still succeed."""
        event = _sample_event(source_ip=None, user_name=None,
                              event_outcome=EventOutcome.SUCCESS)
        inserted = await writer.insert_event(event, idempotency_key="test-004")
        assert inserted is True