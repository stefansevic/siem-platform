"""
Unit tests for the deduplication decision function.

Strategy: build small Incident objects, feed pairs (existing, new)
into decide(), assert action and merged fields.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID, uuid4

import pytest

from shared.ecs_models import Incident, IncidentSeverity, IncidentStatus

from app.dedup import DedupAction, decide


# ============================================
# Helpers
# ============================================

T0 = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_incident(
    *,
    id: Optional[UUID] = None,
    rule_name: str = "brute_force",
    source_ip: str = "1.2.3.4",
    first_event_at: Optional[datetime] = None,
    last_event_at: Optional[datetime] = None,
    event_count: int = 5,
    contributing: Optional[List[UUID]] = None,
    severity: IncidentSeverity = IncidentSeverity.HIGH,
    status: IncidentStatus = IncidentStatus.OPEN,
) -> Incident:
    return Incident(
        id=id or uuid4(),
        rule_name=rule_name,
        rule_version="1.0",
        severity=severity,
        first_event_at=first_event_at or at(0),
        last_event_at=last_event_at or at(0),
        source_ip=source_ip,
        event_count=event_count,
        contributing_events=contributing or [],
        status=status,
    )


SILENCE_5MIN = timedelta(minutes=5)


# ============================================
# INSERT cases — no merge
# ============================================

class TestInsertCases:
    def test_no_existing_means_insert(self):
        new_trigger = make_incident()
        result = decide(
            new_trigger=new_trigger,
            existing_open=None,
            silence_window=SILENCE_5MIN,
        )
        assert result.action == DedupAction.INSERT
        # The trigger comes through unchanged
        assert result.incident.id == new_trigger.id

    def test_different_rule_name_inserts(self):
        existing = make_incident(rule_name="brute_force")
        new_trigger = make_incident(rule_name="directory_scanning")
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        # Defensive check inside decide(): different rule -> insert
        assert result.action == DedupAction.INSERT

    def test_different_source_ip_inserts(self):
        existing = make_incident(source_ip="1.1.1.1")
        new_trigger = make_incident(source_ip="2.2.2.2")
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.action == DedupAction.INSERT

    def test_existing_outside_silence_window_inserts(self):
        """
        Existing incident's last_event_at is 10 minutes before the new
        trigger's last_event_at. With a 5-minute silence window, the
        previous attack is considered concluded and a new incident is
        created.
        """
        existing = make_incident(last_event_at=at(0))
        new_trigger = make_incident(last_event_at=at(600))  # +10 min
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.action == DedupAction.INSERT


# ============================================
# UPDATE cases — merge
# ============================================

class TestUpdateCases:
    def test_within_silence_window_updates(self):
        existing = make_incident(
            last_event_at=at(0),
            event_count=5,
        )
        new_trigger = make_incident(
            last_event_at=at(60),  # +1 min, well within 5
            event_count=6,
        )
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.action == DedupAction.UPDATE
        # Merged incident keeps the existing id
        assert result.incident.id == existing.id
        # last_event_at advances to the new trigger's
        assert result.incident.last_event_at == at(60)
        # event_count climbs to the higher value
        assert result.incident.event_count == 6

    def test_at_exact_silence_boundary_updates(self):
        """An age of exactly silence_window is still within tolerance."""
        existing = make_incident(last_event_at=at(0))
        new_trigger = make_incident(last_event_at=at(300))  # exactly +5 min
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.action == DedupAction.UPDATE

    def test_one_microsecond_past_boundary_inserts(self):
        existing = make_incident(last_event_at=at(0))
        new_trigger = make_incident(last_event_at=at(300.000001))
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.action == DedupAction.INSERT


# ============================================
# Merge correctness
# ============================================

class TestMergeCorrectness:
    def test_first_event_at_preserved_from_existing(self):
        existing = make_incident(
            first_event_at=at(0),
            last_event_at=at(10),
        )
        new_trigger = make_incident(
            first_event_at=at(50),  # would normally be later
            last_event_at=at(60),
        )
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        # first_event_at stays anchored to the original detection
        assert result.incident.first_event_at == at(0)

    def test_event_count_takes_max(self):
        """
        Correlator emits cumulative counts, so the new trigger's count
        is always >= existing. We use max() defensively in case of
        out-of-order delivery.
        """
        existing = make_incident(event_count=10)
        new_trigger = make_incident(
            last_event_at=at(60),
            event_count=4,  # Out-of-order: smaller than existing
        )
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.incident.event_count == 10  # max wins

    def test_contributing_events_unioned(self):
        u1, u2, u3, u4 = uuid4(), uuid4(), uuid4(), uuid4()
        existing = make_incident(contributing=[u1, u2])
        new_trigger = make_incident(
            last_event_at=at(60),
            contributing=[u2, u3, u4],  # u2 overlaps
        )
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        # First-seen order, no duplicates
        assert result.incident.contributing_events == [u1, u2, u3, u4]

    def test_severity_and_status_preserved_from_existing(self):
        """
        Operator may have already triaged the incident; the new trigger
        should not silently downgrade severity or change status.
        """
        existing = make_incident(severity=IncidentSeverity.CRITICAL)
        new_trigger = make_incident(severity=IncidentSeverity.LOW)
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.action == DedupAction.UPDATE
        # CRITICAL preserved despite new trigger being LOW
        assert result.incident.severity == IncidentSeverity.CRITICAL.value

    def test_details_taken_from_new_trigger(self):
        """
        details contains the latest snapshot of rule context (sample
        paths, threshold, etc.). The freshest one wins.
        """
        existing = make_incident()
        existing = existing.model_copy(update={
            "details": {"distinct_paths_count": 10}
        })
        new_trigger = make_incident(last_event_at=at(60))
        new_trigger = new_trigger.model_copy(update={
            "details": {"distinct_paths_count": 25}
        })
        result = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=SILENCE_5MIN,
        )
        assert result.incident.details["distinct_paths_count"] == 25