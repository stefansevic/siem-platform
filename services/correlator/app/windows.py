"""
Sliding window data structure for correlation rules.

A SlidingWindow holds a sequence of (timestamp, event) entries and
automatically evicts entries older than its configured duration.

The structure is intentionally generic: it does not know what an "event"
is. Rules instantiate one window per (rule, subject) pair — for example,
one SlidingWindow of failure events per source IP for the brute-force
rule.

Design notes:
    * deque is used for O(1) append and O(1) popleft, which fits the
      "newest at one end, oldest at the other" access pattern.
    * Eviction is lazy: it happens at the start of every add() and
      every count() call. Idle subjects do not retain memory until
      they are accessed; an explicit prune() helper exists for the
      engine's periodic cleanup pass.
    * Time is passed in by the caller (no datetime.now() inside) so
      tests can advance time deterministically.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Iterator, Optional


@dataclass(frozen=True)
class WindowEntry:
    """One entry in a sliding window."""
    timestamp: datetime
    event: Any  # Whatever the rule needs to retain (ECSEvent, dict, etc.)


class SlidingWindow:
    """
    A bounded-time deque of events.

    Args:
        duration: how far back from "now" the window keeps entries.
                  Entries older than (now - duration) are evicted.
    """

    def __init__(self, duration: timedelta):
        if duration.total_seconds() <= 0:
            raise ValueError("duration must be positive")
        self._duration = duration
        self._entries: deque[WindowEntry] = deque()

    # ----- Mutators -----

    def add(self, timestamp: datetime, event: Any) -> None:
        """
        Append an entry; evict any entries that have aged out relative
        to the new entry's timestamp. The newest entry's timestamp is
        treated as "now" so windows advance with stream time, not
        wall-clock time.
        """
        self._evict_older_than(timestamp - self._duration)
        self._entries.append(WindowEntry(timestamp, event))

    def prune(self, now: datetime) -> int:
        """
        Force eviction relative to the given `now`. Returns the number
        of entries removed. Used by the engine's periodic cleanup pass.
        """
        before = len(self._entries)
        self._evict_older_than(now - self._duration)
        return before - len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    # ----- Inspectors -----

    def count(self, predicate: Optional[Callable[[Any], bool]] = None) -> int:
        """
        Return the number of entries currently in the window.
        If a predicate is given, only entries whose event satisfies it
        are counted. The window is NOT pruned by this call — counts
        reflect whatever has been added; explicit prune() should be
        used if external callers want stale-free counts.
        """
        if predicate is None:
            return len(self._entries)
        return sum(1 for e in self._entries if predicate(e.event))

    def events(self) -> Iterator[Any]:
        """Iterate over events (without timestamps), oldest first."""
        for entry in self._entries:
            yield entry.event

    def entries(self) -> Iterable[WindowEntry]:
        """Iterate over (timestamp, event) entries, oldest first."""
        return tuple(self._entries)

    def first_timestamp(self) -> Optional[datetime]:
        return self._entries[0].timestamp if self._entries else None

    def last_timestamp(self) -> Optional[datetime]:
        return self._entries[-1].timestamp if self._entries else None

    @property
    def duration(self) -> timedelta:
        return self._duration

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    # ----- Internal -----

    def _evict_older_than(self, cutoff: datetime) -> None:
        """Pop entries whose timestamp is strictly less than cutoff."""
        while self._entries and self._entries[0].timestamp < cutoff:
            self._entries.popleft()