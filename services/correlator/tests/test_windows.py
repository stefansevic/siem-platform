"""
Unit tests for the SlidingWindow data structure.

These tests use synthetic timestamps so behavior is deterministic
regardless of wall-clock time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.windows import SlidingWindow, WindowEntry


# Fixed reference instant; all test timestamps are offsets from this.
T0 = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    """Helper: timestamp at T0 + seconds."""
    return T0 + timedelta(seconds=seconds)


# ============================================
# Construction
# ============================================

class TestConstruction:
    def test_zero_duration_raises(self):
        with pytest.raises(ValueError):
            SlidingWindow(timedelta(0))

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError):
            SlidingWindow(timedelta(seconds=-1))

    def test_empty_window_has_zero_length(self):
        w = SlidingWindow(timedelta(minutes=1))
        assert len(w) == 0
        assert not w  # __bool__
        assert w.first_timestamp() is None
        assert w.last_timestamp() is None


# ============================================
# add() and count()
# ============================================

class TestAddAndCount:
    def test_add_single_entry(self):
        w = SlidingWindow(timedelta(minutes=1))
        w.add(at(0), "event-a")

        assert len(w) == 1
        assert w.first_timestamp() == at(0)
        assert w.last_timestamp() == at(0)

    def test_count_with_no_predicate_returns_total(self):
        w = SlidingWindow(timedelta(minutes=5))
        w.add(at(0), "a")
        w.add(at(10), "b")
        w.add(at(20), "c")
        assert w.count() == 3

    def test_count_with_predicate_filters(self):
        w = SlidingWindow(timedelta(minutes=5))
        w.add(at(0), {"outcome": "failure"})
        w.add(at(10), {"outcome": "success"})
        w.add(at(20), {"outcome": "failure"})

        only_failures = w.count(lambda ev: ev["outcome"] == "failure")
        assert only_failures == 2


# ============================================
# Eviction
# ============================================

class TestEviction:
    def test_old_entries_evicted_when_new_arrives(self):
        """
        Adding an entry advances the window: anything older than
        (new_timestamp - duration) is evicted.
        """
        w = SlidingWindow(timedelta(seconds=60))
        w.add(at(0), "old")
        w.add(at(30), "still-fresh")
        # New entry at t=90s; cutoff = 90 - 60 = 30. Entries at < 30 are evicted.
        w.add(at(90), "newest")

        events = list(w.events())
        assert events == ["still-fresh", "newest"]
        assert len(w) == 2

    def test_entry_exactly_at_boundary_kept(self):
        """An entry whose timestamp == cutoff stays (strict-less eviction)."""
        w = SlidingWindow(timedelta(seconds=60))
        w.add(at(0), "boundary")
        w.add(at(60), "newer")  # cutoff is exactly t=0; t=0 is NOT evicted
        assert len(w) == 2

    def test_entry_one_microsecond_past_boundary_evicted(self):
        w = SlidingWindow(timedelta(seconds=60))
        w.add(at(0), "old")
        w.add(at(60.000001), "newer")  # cutoff slightly after t=0
        events = list(w.events())
        assert events == ["newer"]

    def test_explicit_prune(self):
        """prune(now) evicts entries older than now-duration without adding."""
        w = SlidingWindow(timedelta(seconds=60))
        w.add(at(0), "a")
        w.add(at(30), "b")
        w.add(at(50), "c")

        removed = w.prune(at(91))  # cutoff = 31; entries at 0 and 30 evicted
        assert removed == 2
        assert list(w.events()) == ["c"]

    def test_prune_when_nothing_to_remove(self):
        w = SlidingWindow(timedelta(seconds=60))
        w.add(at(0), "a")
        w.add(at(30), "b")

        removed = w.prune(at(45))  # cutoff = -15; nothing evicted
        assert removed == 0
        assert len(w) == 2


# ============================================
# Iteration & inspection
# ============================================

class TestIteration:
    def test_events_iterates_oldest_first(self):
        w = SlidingWindow(timedelta(minutes=10))
        w.add(at(0), "first")
        w.add(at(10), "second")
        w.add(at(20), "third")
        assert list(w.events()) == ["first", "second", "third"]

    def test_entries_returns_window_entries_with_timestamps(self):
        w = SlidingWindow(timedelta(minutes=10))
        w.add(at(0), "a")
        w.add(at(10), "b")

        entries = w.entries()
        assert len(entries) == 2
        assert entries[0] == WindowEntry(at(0), "a")
        assert entries[1] == WindowEntry(at(10), "b")

    def test_first_and_last_timestamp(self):
        w = SlidingWindow(timedelta(minutes=10))
        w.add(at(5), "a")
        w.add(at(15), "b")
        w.add(at(25), "c")
        assert w.first_timestamp() == at(5)
        assert w.last_timestamp() == at(25)


# ============================================
# Clear
# ============================================

class TestClear:
    def test_clear_empties_window(self):
        w = SlidingWindow(timedelta(minutes=5))
        w.add(at(0), "a")
        w.add(at(10), "b")

        w.clear()
        assert len(w) == 0
        assert list(w.events()) == []
        assert w.first_timestamp() is None