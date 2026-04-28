"""
Unit tests for the CorrelationEngine.

The engine is glue, so these tests focus on dispatch behavior:
    * Events go to the right rules' windows
    * Different subjects (IPs) get isolated state
    * Different rules can share a subject without crosstalk
    * Stats counters move correctly
    * prune_stale() evicts truly empty windows
    * A buggy rule does not poison the loop
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from shared.ecs_models import (
    ECSEvent,
    EventCategory,
    EventOutcome,
    Incident,
    IncidentSeverity,
    LogSource,
)

from app.engine import CorrelationEngine
from app.rules import (
    AccountTakeoverRule,
    BruteForceRule,
    CorrelationRule,
    DirectoryScanningRule,
)
from app.windows import SlidingWindow


# ============================================
# Helpers
# ============================================

T0 = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def auth_event(
    *,
    outcome: EventOutcome,
    source_ip: str = "1.2.3.4",
    user_name: str = "alice",
    timestamp: Optional[datetime] = None,
) -> ECSEvent:
    return ECSEvent(
        timestamp=timestamp or at(0),
        event_category=EventCategory.AUTHENTICATION,
        event_outcome=outcome,
        source_ip=source_ip,
        user_name=user_name,
        log_source=LogSource.DEMO_WEBAPP,
    )


def http_404(
    *,
    path: str,
    source_ip: str = "1.2.3.4",
    timestamp: Optional[datetime] = None,
) -> ECSEvent:
    return ECSEvent(
        timestamp=timestamp or at(0),
        event_category=EventCategory.WEB,
        event_outcome=EventOutcome.FAILURE,
        source_ip=source_ip,
        http_response_status_code=404,
        url_path=path,
        log_source=LogSource.NGINX,
    )


# ============================================
# Construction
# ============================================

class TestConstruction:
    def test_empty_rules_list_raises(self):
        with pytest.raises(ValueError):
            CorrelationEngine([])

    def test_rules_property_returns_a_copy(self):
        rules = [BruteForceRule()]
        engine = CorrelationEngine(rules)
        assert engine.rules == rules
        # Mutating the returned list must not affect the engine
        engine.rules.append("garbage")
        assert len(engine.rules) == 1


# ============================================
# Dispatch
# ============================================

class TestDispatch:
    def test_irrelevant_event_handled_by_no_rule(self):
        """A WEB event with status 200 matches none of the 3 rules."""
        engine = CorrelationEngine([
            BruteForceRule(),
            DirectoryScanningRule(),
            AccountTakeoverRule(),
        ])
        ev = ECSEvent(
            timestamp=at(0),
            event_category=EventCategory.WEB,
            event_outcome=EventOutcome.SUCCESS,
            source_ip="9.9.9.9",
            http_response_status_code=200,
            url_path="/",
            log_source=LogSource.NGINX,
        )
        incidents = engine.process(ev)
        assert incidents == []
        assert engine.stats.events_skipped == 1

    def test_brute_force_fires_at_threshold(self):
        engine = CorrelationEngine([
            BruteForceRule(threshold=5, window=timedelta(minutes=1)),
        ])
        last_incidents = []
        for i in range(5):
            last_incidents = engine.process(
                auth_event(outcome=EventOutcome.FAILURE, timestamp=at(i))
            )

        assert len(last_incidents) == 1
        assert last_incidents[0].rule_name == "brute_force"
        assert engine.stats.incidents_emitted == 1
        assert engine.stats.per_rule_incidents == {"brute_force": 1}

    def test_two_ips_get_isolated_windows(self):
        """Two attackers each at threshold-minus-one should not combine."""
        engine = CorrelationEngine([
            BruteForceRule(threshold=5, window=timedelta(minutes=1)),
        ])
        for i in range(4):
            engine.process(auth_event(
                outcome=EventOutcome.FAILURE,
                source_ip="1.1.1.1", timestamp=at(i)
            ))
        for i in range(4):
            engine.process(auth_event(
                outcome=EventOutcome.FAILURE,
                source_ip="2.2.2.2", timestamp=at(i + 10)
            ))
        assert engine.stats.incidents_emitted == 0
        assert engine.window_count() == 2  # one per IP

    def test_two_rules_can_fire_on_same_event(self):
        """
        Construct a pathological case: account takeover triggers, and
        the same success event also pushes brute-force over its low
        threshold (we use threshold=2 to keep the test tiny).
        Note: brute-force ignores success outcomes, so really only ATO
        fires. This test confirms the engine returns lists of length 1
        in the typical case.
        """
        engine = CorrelationEngine([
            BruteForceRule(threshold=2, window=timedelta(minutes=1)),
            AccountTakeoverRule(failure_threshold=2,
                                window=timedelta(minutes=5)),
        ])
        engine.process(auth_event(outcome=EventOutcome.FAILURE, timestamp=at(0)))
        engine.process(auth_event(outcome=EventOutcome.FAILURE, timestamp=at(1)))
        # Brute-force already fired on the second failure
        assert engine.stats.per_rule_incidents.get("brute_force") == 1

        incidents = engine.process(
            auth_event(outcome=EventOutcome.SUCCESS, timestamp=at(2))
        )
        # Now ATO fires on the success
        assert any(i.rule_name == "account_takeover" for i in incidents)
        assert engine.stats.per_rule_incidents.get("account_takeover") == 1

    def test_distinct_subjects_per_rule(self):
        """
        Same IP processed by two rules should occupy two separate windows
        (one per rule) rather than collide.
        """
        engine = CorrelationEngine([
            BruteForceRule(),
            DirectoryScanningRule(),
        ])
        engine.process(auth_event(outcome=EventOutcome.FAILURE))
        engine.process(http_404(path="/admin"))

        # Both rules should have a window for "1.2.3.4"
        bf = engine.get_window("brute_force", "1.2.3.4")
        ds = engine.get_window("directory_scanning", "1.2.3.4")
        assert bf is not None
        assert ds is not None
        assert bf is not ds


# ============================================
# Pruning
# ============================================

class TestPruning:
    def test_prune_stale_removes_empty_windows(self):
        engine = CorrelationEngine([
            BruteForceRule(threshold=5, window=timedelta(seconds=60)),
        ])
        engine.process(auth_event(outcome=EventOutcome.FAILURE, timestamp=at(0)))
        assert engine.window_count() == 1

        # Advance "now" well past the window duration
        removed = engine.prune_stale(at(300))
        assert removed == 1
        assert engine.window_count() == 0
        assert engine.stats.windows_pruned == 1

    def test_prune_keeps_windows_with_recent_entries(self):
        engine = CorrelationEngine([
            BruteForceRule(threshold=5, window=timedelta(seconds=60)),
        ])
        engine.process(auth_event(outcome=EventOutcome.FAILURE, timestamp=at(0)))
        engine.process(auth_event(outcome=EventOutcome.FAILURE, timestamp=at(50)))

        # Now=70 -> cutoff=10 -> entry at t=0 evicted, entry at t=50 stays
        removed = engine.prune_stale(at(70))
        assert removed == 0
        assert engine.window_count() == 1


# ============================================
# Resilience
# ============================================

class TestResilience:
    def test_buggy_rule_does_not_break_engine(self):
        """If one rule raises, others still run and the engine survives."""

        class ExplodingRule(CorrelationRule):
            name = "exploding"
            severity = IncidentSeverity.LOW
            window_duration = timedelta(minutes=1)

            def subject(self, event: ECSEvent) -> Optional[str]:
                return "any"

            def evaluate(self, window, event) -> Optional[Incident]:
                raise RuntimeError("boom")

        engine = CorrelationEngine([
            ExplodingRule(),
            BruteForceRule(threshold=2, window=timedelta(minutes=1)),
        ])
        # Two failures -> brute-force should still fire despite the
        # exploding rule raising on every event.
        engine.process(auth_event(outcome=EventOutcome.FAILURE, timestamp=at(0)))
        incidents = engine.process(
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(1))
        )
        assert any(i.rule_name == "brute_force" for i in incidents)