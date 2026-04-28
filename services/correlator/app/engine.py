"""
Correlation engine: composes rules and per-subject windows.

The engine is the single entry point for incoming events. It owns the
in-memory state for all rules and dispatches each event to the windows
that care about it.

State layout:
    self._windows: Dict[(rule_name, subject_key), SlidingWindow]

    Each rule gets its own keyspace via the rule_name prefix; subjects
    (typically source IPs) are isolated per-rule, so the same IP can
    have a brute-force window AND a directory-scanning window without
    crosstalk.

Threading model:
    Single-threaded by design. The Redis consumer is async and processes
    events sequentially, so there is no concurrent access to the state
    dict. If the project ever needs parallel evaluation, this is the
    place to add a lock or shard by subject hash.

Periodic cleanup:
    Windows for inactive subjects keep occupying memory until pruned.
    The engine exposes prune_stale() which the consumer calls every
    N seconds to evict subjects whose latest entry has aged out
    completely (the window itself is empty).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from shared.ecs_models import ECSEvent, Incident

from app.rules import CorrelationRule
from app.windows import SlidingWindow

logger = logging.getLogger(__name__)


# ============================================
# Stats (cheap observability)
# ============================================

@dataclass
class EngineStats:
    """Lightweight counters for log-line observability."""
    events_processed: int = 0
    events_skipped: int = 0          # No rule cared about them
    incidents_emitted: int = 0
    windows_pruned: int = 0
    per_rule_incidents: dict = field(default_factory=dict)

    def record_incident(self, rule_name: str) -> None:
        self.incidents_emitted += 1
        self.per_rule_incidents[rule_name] = (
            self.per_rule_incidents.get(rule_name, 0) + 1
        )


# ============================================
# Engine
# ============================================

class CorrelationEngine:
    """
    Orchestrates rule evaluation across all incoming ECSEvents.

    Args:
        rules: list of CorrelationRule instances. Order does not matter
               functionally; it only affects the order of incidents
               returned for a single event (rare in practice).
    """

    def __init__(self, rules: Iterable[CorrelationRule]):
        self._rules: List[CorrelationRule] = list(rules)
        # Key: (rule_name, subject_key) -> SlidingWindow
        self._windows: dict[Tuple[str, str], SlidingWindow] = {}
        self.stats = EngineStats()

        if not self._rules:
            raise ValueError("CorrelationEngine needs at least one rule")

    @property
    def rules(self) -> List[CorrelationRule]:
        return list(self._rules)

    # ----- main API -----

    def process(self, event: ECSEvent) -> List[Incident]:
        """
        Dispatch the event to every relevant rule. Returns the list of
        incidents emitted by this single event (usually 0; occasionally 1;
        very rarely more if multiple rules fire on the same event).
        """
        self.stats.events_processed += 1
        incidents: List[Incident] = []
        was_handled = False

        for rule in self._rules:
            subject_key = rule.subject(event)
            if subject_key is None:
                continue
            was_handled = True

            window = self._get_or_create_window(rule, subject_key)
            window.add(event.timestamp, event)

            try:
                incident = rule.evaluate(window, event)
            except Exception:
                # A buggy rule should never bring down the engine.
                logger.exception(
                    "rule %s raised on event id=%s; skipping",
                    rule.name, event.id,
                )
                continue

            if incident is not None:
                incidents.append(incident)
                self.stats.record_incident(rule.name)

        if not was_handled:
            self.stats.events_skipped += 1

        return incidents

    def prune_stale(self, now: datetime) -> int:
        """
        Evict empty windows. A window is considered stale once all its
        entries have aged out relative to `now`. Returns the number of
        windows removed.
        """
        to_remove = []
        for key, window in self._windows.items():
            window.prune(now)
            if len(window) == 0:
                to_remove.append(key)

        for key in to_remove:
            del self._windows[key]

        self.stats.windows_pruned += len(to_remove)
        return len(to_remove)

    # ----- introspection (used in tests and debug logging) -----

    def window_count(self) -> int:
        return len(self._windows)

    def get_window(
        self, rule_name: str, subject_key: str,
    ) -> Optional[SlidingWindow]:
        return self._windows.get((rule_name, subject_key))

    # ----- internals -----

    def _get_or_create_window(
        self, rule: CorrelationRule, subject_key: str,
    ) -> SlidingWindow:
        key = (rule.name, subject_key)
        window = self._windows.get(key)
        if window is None:
            window = SlidingWindow(rule.window_duration)
            self._windows[key] = window
        return window