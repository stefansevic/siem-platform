"""
Correlation rules.

Each rule is a self-contained class with two responsibilities:

    subject(event)   -> str | None
        Identifies which "actor" the event is about. The engine groups
        events by subject so each subject gets its own SlidingWindow.
        Returns None if the event is not relevant to this rule (e.g.
        a successful login is irrelevant to brute-force detection).

    evaluate(window, event) -> Incident | None
        Given the existing window for this subject and the freshly
        added event, decides whether the rule has fired. Returns a
        populated Incident on a hit, or None.

The engine does not know what each rule looks for. It just walks the
list of registered rules, dispatches events, and forwards Incidents.

Design notes:
    * Rules are instantiated once at startup with their threshold
      parameters. Re-tuning thresholds is a config change, not a code
      change.
    * Rules are I/O-free. They only inspect the SlidingWindow they were
      handed and produce a return value. Persistence and notification
      are the engine's and Alert Manager's jobs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Optional
from uuid import uuid4

from shared.ecs_models import (
    ECSEvent,
    EventCategory,
    EventOutcome,
    Incident,
    IncidentSeverity,
)

from app.windows import SlidingWindow


# ============================================
# Base class
# ============================================

class CorrelationRule(ABC):
    """Abstract base for all correlation rules."""

    name: str
    version: str = "1.0"
    severity: IncidentSeverity
    window_duration: timedelta

    @abstractmethod
    def subject(self, event: ECSEvent) -> Optional[str]:
        """
        Return the grouping key for this event, or None if the rule
        does not care about this event at all.
        """

    @abstractmethod
    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        """
        Apply rule logic. The given window contains all relevant prior
        events for this subject (already filtered by subject()), with
        the new event already added at the end.
        """

    # ----- shared helper -----

    def _build_incident(
        self,
        window: SlidingWindow,
        event: ECSEvent,
        subject_key: str,
        details: Optional[dict] = None,
    ) -> Incident:
        """
        Common Incident scaffolding. Subclasses fill in `details` with
        rule-specific context.
        """
        contributing = [e.id for e in window.events() if isinstance(e, ECSEvent)]
        return Incident(
            id=uuid4(),
            rule_name=self.name,
            rule_version=self.version,
            severity=self.severity,
            first_event_at=window.first_timestamp() or event.timestamp,
            last_event_at=event.timestamp,
            source_ip=event.source_ip,
            target_user_name=event.user_name,
            event_count=window.count(),
            details=details or {},
            contributing_events=contributing,
        )


# ============================================
# Rule 1: Brute-Force Detection
# ============================================

class BruteForceRule(CorrelationRule):
    """
    Fires when a single source IP produces too many failed
    authentication attempts within a short window.

    Specification: X failures from the same source IP within T minutes.
    Defaults from the project brief: X = 5, T = 1 minute.

    Rationale: classic password-guessing attack. The rule looks at
    `event.category = authentication` AND `event.outcome = failure`,
    grouped by `source.ip`. Successful logins or non-auth events are
    ignored entirely (subject() returns None).
    """

    name = "brute_force"
    severity = IncidentSeverity.HIGH

    def __init__(self, *, threshold: int = 5, window: timedelta = timedelta(minutes=1)):
        if threshold < 2:
            raise ValueError("threshold must be at least 2")
        self.threshold = threshold
        self.window_duration = window

    def subject(self, event: ECSEvent) -> Optional[str]:
        if event.event_category != EventCategory.AUTHENTICATION.value:
            return None
        if event.event_outcome != EventOutcome.FAILURE.value:
            return None
        if event.source_ip is None:
            return None
        return str(event.source_ip)

    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        # Window already contains failure events for this IP only,
        # because subject() filtered everything else out.
        if window.count() < self.threshold:
            return None

        # Distinct usernames attempted are useful forensic context.
        usernames = sorted({
            e.user_name for e in window.events()
            if isinstance(e, ECSEvent) and e.user_name
        })
        return self._build_incident(
            window=window,
            event=event,
            subject_key=str(event.source_ip),
            details={
                "threshold": self.threshold,
                "window_seconds": int(self.window_duration.total_seconds()),
                "failure_count": window.count(),
                "distinct_usernames": usernames,
            },
        )


# ============================================
# Rule 2: Resource Enumeration (Directory Scanning)
# ============================================

class DirectoryScanningRule(CorrelationRule):
    """
    Fires when a single source IP probes many distinct paths and
    receives 404 responses, optionally followed by 403s.

    Specification: > 10 distinct 404 paths from the same source IP
    within 30 seconds.

    Rationale: tools like DirBuster/Gobuster brute-force common paths
    looking for hidden endpoints. The hallmark is a wide variety of
    paths, not just a high request rate. Counting distinct URLs
    (rather than total 404s) reduces false positives from a misconfigured
    client retrying the same broken URL.
    """

    name = "directory_scanning"
    severity = IncidentSeverity.MEDIUM

    def __init__(
        self,
        *,
        threshold: int = 10,
        window: timedelta = timedelta(seconds=30),
    ):
        if threshold < 2:
            raise ValueError("threshold must be at least 2")
        self.threshold = threshold
        self.window_duration = window

    def subject(self, event: ECSEvent) -> Optional[str]:
        # Only 404 (not found) matters for distinct-path scanning.
        # 403 (forbidden) is the spec's "follow-up" hint and could be
        # added in a future iteration as additional context.
        if event.http_response_status_code != 404:
            return None
        if event.source_ip is None:
            return None
        if not event.url_path:
            return None
        return str(event.source_ip)

    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        distinct_paths = {
            e.url_path for e in window.events()
            if isinstance(e, ECSEvent) and e.url_path
        }
        if len(distinct_paths) < self.threshold:
            return None

        return self._build_incident(
            window=window,
            event=event,
            subject_key=str(event.source_ip),
            details={
                "threshold": self.threshold,
                "window_seconds": int(self.window_duration.total_seconds()),
                "distinct_paths_count": len(distinct_paths),
                "sample_paths": sorted(distinct_paths)[:20],
            },
        )


# ============================================
# Rule 3: Account Takeover Pattern
# ============================================

class AccountTakeoverRule(CorrelationRule):
    """
    Fires when a series of failed logins from one source IP is followed
    by a successful login from the same IP shortly after.

    Specification: failures followed by a success from the same IP
    within a short window.

    Rationale: attacker guesses correctly on attempt N+1. Pure brute
    force (failures only) misses the moment the breach succeeds; this
    rule explicitly captures the success that follows.

    Implementation: subject() accepts BOTH auth failures and auth
    successes for the same source IP, so the window holds the full
    sequence. evaluate() fires on a success when at least
    `failure_threshold` failures preceded it within the window.
    """

    name = "account_takeover"
    severity = IncidentSeverity.CRITICAL

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        window: timedelta = timedelta(minutes=5),
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.failure_threshold = failure_threshold
        self.window_duration = window

    def subject(self, event: ECSEvent) -> Optional[str]:
        if event.event_category != EventCategory.AUTHENTICATION.value:
            return None
        if event.source_ip is None:
            return None
        # Both successes and failures relevant; mapping decides outcome.
        if event.event_outcome not in (
            EventOutcome.SUCCESS.value, EventOutcome.FAILURE.value,
        ):
            return None
        return str(event.source_ip)

    def evaluate(
        self, window: SlidingWindow, event: ECSEvent,
    ) -> Optional[Incident]:
        # Only a success triggers; otherwise we just accumulate state.
        if event.event_outcome != EventOutcome.SUCCESS.value:
            return None

        # Count failures in the window EXCLUDING the new success itself.
        # Note: window.events() is oldest-first; the last entry is the
        # event we just added.
        prior_failures = [
            e for e in list(window.events())[:-1]
            if isinstance(e, ECSEvent)
            and e.event_outcome == EventOutcome.FAILURE.value
        ]
        if len(prior_failures) < self.failure_threshold:
            return None

        return self._build_incident(
            window=window,
            event=event,
            subject_key=str(event.source_ip),
            details={
                "failure_threshold": self.failure_threshold,
                "window_seconds": int(self.window_duration.total_seconds()),
                "preceding_failure_count": len(prior_failures),
                "successful_user_name": event.user_name,
                "attempted_usernames": sorted({
                    f.user_name for f in prior_failures if f.user_name
                }),
            },
        )