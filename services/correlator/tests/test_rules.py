"""
Unit tests for the three correlation rules.

Strategy:
    * Construct synthetic ECSEvent objects directly.
    * Drive a SlidingWindow manually as if the engine had dispatched
      events to it.
    * Assert subject() filtering, threshold edges, and Incident shape.

Naming:
    * "below threshold" -> rule should NOT fire
    * "at threshold"    -> rule should fire on the entry that hits it
    * "above threshold" -> rule should still fire
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from shared.ecs_models import (
    ECSEvent,
    EventCategory,
    EventOutcome,
    IncidentSeverity,
    LogSource,
)

from app.rules import (
    AccountTakeoverRule,
    BruteForceRule,
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
    user_name: Optional[str] = "alice",
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


def http_event(
    *,
    status: int,
    path: str,
    source_ip: str = "1.2.3.4",
    timestamp: Optional[datetime] = None,
) -> ECSEvent:
    return ECSEvent(
        timestamp=timestamp or at(0),
        event_category=EventCategory.WEB,
        event_outcome=EventOutcome.FAILURE if status >= 400 else EventOutcome.SUCCESS,
        source_ip=source_ip,
        http_response_status_code=status,
        url_path=path,
        log_source=LogSource.NGINX,
    )


def feed(rule, window: SlidingWindow, events):
    """Push a sequence of events through the window like the engine would."""
    last_incident = None
    for ev in events:
        window.add(ev.timestamp, ev)
        incident = rule.evaluate(window, ev)
        if incident is not None:
            last_incident = incident
    return last_incident


# ============================================
# BruteForceRule
# ============================================

class TestBruteForceSubject:
    def test_failure_returns_source_ip(self):
        rule = BruteForceRule()
        ev = auth_event(outcome=EventOutcome.FAILURE)
        assert rule.subject(ev) == "1.2.3.4"

    def test_success_returns_none(self):
        rule = BruteForceRule()
        ev = auth_event(outcome=EventOutcome.SUCCESS)
        assert rule.subject(ev) is None

    def test_non_auth_event_returns_none(self):
        rule = BruteForceRule()
        ev = http_event(status=404, path="/x")
        assert rule.subject(ev) is None

    def test_event_without_source_ip_returns_none(self):
        rule = BruteForceRule()
        ev = ECSEvent(
            timestamp=at(0),
            event_category=EventCategory.AUTHENTICATION,
            event_outcome=EventOutcome.FAILURE,
            log_source=LogSource.DEMO_WEBAPP,
        )
        assert rule.subject(ev) is None


class TestBruteForceEvaluate:
    def test_below_threshold_does_not_fire(self):
        rule = BruteForceRule(threshold=5, window=timedelta(minutes=1))
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(i))
            for i in range(4)
        ]
        assert feed(rule, w, events) is None

    def test_at_threshold_fires(self):
        rule = BruteForceRule(threshold=5, window=timedelta(minutes=1))
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(i))
            for i in range(5)
        ]
        incident = feed(rule, w, events)
        assert incident is not None
        assert incident.rule_name == "brute_force"
        assert incident.severity == IncidentSeverity.HIGH.value
        assert str(incident.source_ip) == "1.2.3.4"
        assert incident.event_count == 5

    def test_above_threshold_fires(self):
        rule = BruteForceRule(threshold=5, window=timedelta(minutes=1))
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(i))
            for i in range(8)
        ]
        incident = feed(rule, w, events)
        assert incident is not None
        assert incident.event_count == 8

    def test_distinct_usernames_in_details(self):
        rule = BruteForceRule(threshold=3, window=timedelta(minutes=1))
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, user_name="alice", timestamp=at(0)),
            auth_event(outcome=EventOutcome.FAILURE, user_name="bob", timestamp=at(1)),
            auth_event(outcome=EventOutcome.FAILURE, user_name="alice", timestamp=at(2)),
        ]
        incident = feed(rule, w, events)
        assert incident is not None
        assert incident.details["distinct_usernames"] == ["alice", "bob"]

    def test_old_failures_age_out_and_rule_does_not_fire(self):
        """5 failures total, but spread across 5 minutes with 1-min window."""
        rule = BruteForceRule(threshold=5, window=timedelta(minutes=1))
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(i * 90))
            for i in range(5)
        ]
        # Each new event evicts the previous one; window never has 5 entries.
        assert feed(rule, w, events) is None

    def test_constructor_rejects_threshold_below_two(self):
        with pytest.raises(ValueError):
            BruteForceRule(threshold=1)


# ============================================
# DirectoryScanningRule
# ============================================

class TestDirectoryScanningSubject:
    def test_404_returns_source_ip(self):
        rule = DirectoryScanningRule()
        ev = http_event(status=404, path="/admin")
        assert rule.subject(ev) == "1.2.3.4"

    def test_200_returns_none(self):
        rule = DirectoryScanningRule()
        ev = http_event(status=200, path="/")
        assert rule.subject(ev) is None

    def test_403_returns_none(self):
        """403 not in scope for this iteration; future enhancement."""
        rule = DirectoryScanningRule()
        ev = http_event(status=403, path="/admin")
        assert rule.subject(ev) is None

    def test_event_without_path_returns_none(self):
        rule = DirectoryScanningRule()
        ev = http_event(status=404, path="")
        # Empty path -> not a meaningful scan signal
        assert rule.subject(ev) is None


class TestDirectoryScanningEvaluate:
    def test_distinct_404_paths_above_threshold_fires(self):
        rule = DirectoryScanningRule(threshold=10, window=timedelta(seconds=30))
        w = SlidingWindow(rule.window_duration)
        events = [
            http_event(status=404, path=f"/path-{i}", timestamp=at(i))
            for i in range(11)
        ]
        incident = feed(rule, w, events)
        assert incident is not None
        assert incident.rule_name == "directory_scanning"
        assert incident.severity == IncidentSeverity.MEDIUM.value
        assert incident.details["distinct_paths_count"] == 11

    def test_repeated_same_path_does_not_fire(self):
        """11 hits but on only 1 distinct path — rule must NOT fire."""
        rule = DirectoryScanningRule(threshold=10, window=timedelta(seconds=30))
        w = SlidingWindow(rule.window_duration)
        events = [
            http_event(status=404, path="/admin", timestamp=at(i))
            for i in range(11)
        ]
        assert feed(rule, w, events) is None

    def test_paths_outside_window_do_not_count(self):
        rule = DirectoryScanningRule(threshold=10, window=timedelta(seconds=30))
        w = SlidingWindow(rule.window_duration)
        # 5 paths at t=0..4, then 6 paths at t=60..65; only the second group
        # is in-window (window is 30s, so cutoff at t=65 is t=35 -> earlier
        # group evicted).
        events = [
            http_event(status=404, path=f"/old-{i}", timestamp=at(i))
            for i in range(5)
        ] + [
            http_event(status=404, path=f"/new-{i}", timestamp=at(60 + i))
            for i in range(6)
        ]
        assert feed(rule, w, events) is None  # only 6 fresh paths

    def test_sample_paths_capped_at_20(self):
        rule = DirectoryScanningRule(threshold=10, window=timedelta(seconds=30))
        w = SlidingWindow(rule.window_duration)
        events = [
            http_event(status=404, path=f"/p-{i:03d}", timestamp=at(i * 0.1))
            for i in range(50)
        ]
        incident = feed(rule, w, events)
        assert incident is not None
        assert len(incident.details["sample_paths"]) == 20


# ============================================
# AccountTakeoverRule
# ============================================

class TestAccountTakeoverSubject:
    def test_failure_returns_source_ip(self):
        rule = AccountTakeoverRule()
        ev = auth_event(outcome=EventOutcome.FAILURE)
        assert rule.subject(ev) == "1.2.3.4"

    def test_success_returns_source_ip(self):
        """Both outcomes accepted — success IS the trigger condition."""
        rule = AccountTakeoverRule()
        ev = auth_event(outcome=EventOutcome.SUCCESS)
        assert rule.subject(ev) == "1.2.3.4"

    def test_non_auth_returns_none(self):
        rule = AccountTakeoverRule()
        ev = http_event(status=200, path="/")
        assert rule.subject(ev) is None


class TestAccountTakeoverEvaluate:
    def test_failures_followed_by_success_fires(self):
        rule = AccountTakeoverRule(
            failure_threshold=3, window=timedelta(minutes=5),
        )
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, user_name="alice", timestamp=at(0)),
            auth_event(outcome=EventOutcome.FAILURE, user_name="bob",   timestamp=at(10)),
            auth_event(outcome=EventOutcome.FAILURE, user_name="carol", timestamp=at(20)),
            auth_event(outcome=EventOutcome.SUCCESS, user_name="admin", timestamp=at(30)),
        ]
        incident = feed(rule, w, events)
        assert incident is not None
        assert incident.rule_name == "account_takeover"
        assert incident.severity == IncidentSeverity.CRITICAL.value
        assert incident.details["preceding_failure_count"] == 3
        assert incident.details["successful_user_name"] == "admin"
        assert incident.details["attempted_usernames"] == ["alice", "bob", "carol"]

    def test_success_with_too_few_prior_failures_does_not_fire(self):
        rule = AccountTakeoverRule(
            failure_threshold=3, window=timedelta(minutes=5),
        )
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(0)),
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(10)),
            auth_event(outcome=EventOutcome.SUCCESS, timestamp=at(20)),
        ]
        assert feed(rule, w, events) is None

    def test_only_failures_does_not_fire(self):
        """Brute-force territory; this rule only fires on success."""
        rule = AccountTakeoverRule(
            failure_threshold=3, window=timedelta(minutes=5),
        )
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(i))
            for i in range(10)
        ]
        assert feed(rule, w, events) is None

    def test_only_success_does_not_fire(self):
        rule = AccountTakeoverRule(
            failure_threshold=3, window=timedelta(minutes=5),
        )
        w = SlidingWindow(rule.window_duration)
        events = [auth_event(outcome=EventOutcome.SUCCESS, timestamp=at(0))]
        assert feed(rule, w, events) is None

    def test_failures_aged_out_before_success_does_not_fire(self):
        """Failures happen at t=0, success at t=400s with a 5-min window."""
        rule = AccountTakeoverRule(
            failure_threshold=3, window=timedelta(minutes=5),
        )
        w = SlidingWindow(rule.window_duration)
        events = [
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(0)),
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(1)),
            auth_event(outcome=EventOutcome.FAILURE, timestamp=at(2)),
            auth_event(outcome=EventOutcome.SUCCESS, timestamp=at(400)),
        ]
        # Cutoff at t=400 is t=100 -> all 3 failures evicted
        assert feed(rule, w, events) is None