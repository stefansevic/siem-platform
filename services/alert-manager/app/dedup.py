"""
Pure deduplication logic for incoming incidents.

The Alert Manager sees a stream of Incident triggers from the Correlator.
Many of them describe the same attack episode — six failed logins from
one IP produce two triggers, an eleven-path directory scan produces
twenty. Persisting each trigger as a fresh row would flood the operator
view.

This module decides, for each new trigger, whether it should be merged
into an existing OPEN incident or stored as a new one. The decision is
purely a function of (existing incident, new trigger, silence window),
which means it is trivial to test without Postgres or Redis.

Rules:
    * The match key is (rule_name, source_ip). Two triggers from the
      same rule against the same attacker collapse into one incident.
    * The existing incident must be OPEN. Closed/acknowledged incidents
      do not absorb new triggers — a new attack episode begins.
    * The existing incident's last_event_at must be within the silence
      window relative to the new trigger's last_event_at. If too much
      time has passed, the previous attack is considered finished and
      a new incident is created.

When merging, the result is the merged Incident: the older id and
detected_at are preserved (so the operator sees a single record from
the start), while last_event_at, event_count, and contributing_events
are updated to reflect the new trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import List, Optional
from uuid import UUID

from shared.ecs_models import Incident


# ============================================
# Decision types
# ============================================

class DedupAction(str, Enum):
    INSERT = "insert"  # No matching open incident; create a new row
    UPDATE = "update"  # Merge into the matching open incident


@dataclass(frozen=True)
class DedupDecision:
    """Result of evaluating a trigger against any existing match."""
    action: DedupAction
    incident: Incident  # If INSERT: the new incident. If UPDATE: the merged one.


# ============================================
# Public API
# ============================================

def decide(
    *,
    new_trigger: Incident,
    existing_open: Optional[Incident],
    silence_window: timedelta,
) -> DedupDecision:
    """
    Decide whether the new trigger should INSERT or UPDATE an existing
    open incident.

    Args:
        new_trigger: the incident the Correlator just emitted.
        existing_open: the most recent OPEN incident matching the
            new_trigger's (rule_name, source_ip), or None if no match
            was found in the database.
        silence_window: how long after `last_event_at` an existing
            incident is considered eligible for merging. Triggers that
            arrive after the silence window starts a new incident.

    Returns:
        DedupDecision with action and the Incident that should be
        persisted (either the new one as-is, or the merged result).
    """
    if existing_open is None:
        return DedupDecision(action=DedupAction.INSERT, incident=new_trigger)

    # Match key sanity check — defensive, the caller should have queried
    # by exactly these fields, but enforce it explicitly.
    if existing_open.rule_name != new_trigger.rule_name:
        return DedupDecision(action=DedupAction.INSERT, incident=new_trigger)

    if existing_open.source_ip != new_trigger.source_ip:
        return DedupDecision(action=DedupAction.INSERT, incident=new_trigger)

    # Silence window check: was the existing incident's last activity
    # recent enough to absorb this trigger?
    age = new_trigger.last_event_at - existing_open.last_event_at
    if age > silence_window:
        return DedupDecision(action=DedupAction.INSERT, incident=new_trigger)

    # Merge.
    merged = _merge(existing_open, new_trigger)
    return DedupDecision(action=DedupAction.UPDATE, incident=merged)


# ============================================
# Internals
# ============================================

def _merge(existing: Incident, new_trigger: Incident) -> Incident:
    """
    Combine an existing open incident with a fresh trigger.

    Preserved from existing:
        id, rule_name, rule_version, severity, source_ip,
        target_user_name, first_event_at, detected_at, status, notes

    Updated from new_trigger:
        last_event_at -> the more recent of the two
        event_count   -> max of existing and new (Correlator already
                         re-counts cumulatively, so taking max is safe)
        details       -> the trigger's details (latest snapshot)
        contributing_events -> deduplicated union of both lists
    """
    new_last = max(existing.last_event_at, new_trigger.last_event_at)
    new_count = max(existing.event_count, new_trigger.event_count)
    contributing = _union_uuids(
        existing.contributing_events or [],
        new_trigger.contributing_events or [],
    )

    return existing.model_copy(update={
        "last_event_at": new_last,
        "event_count": new_count,
        "details": new_trigger.details,
        "contributing_events": contributing,
    })


def _union_uuids(a: List[UUID], b: List[UUID]) -> List[UUID]:
    """Deduplicate while preserving first-seen order."""
    seen: set = set()
    out: List[UUID] = []
    for uid in list(a) + list(b):
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out