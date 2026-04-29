"""
Pydantic response models (DTOs) returned by the API Gateway.

These are intentionally separate from `shared/ecs_models.py`:

    - Internal models (RawLogMessage, ECSEvent, Incident) are tuned for
      service-to-service communication: typed IPs, enum values, UUIDs.
    - API DTOs are tuned for frontend consumption: strings everywhere,
      pagination metadata, no internal-only fields.

Keeping them apart prevents the API surface from breaking every time
an internal model changes shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel


# ============================================
# Events
# ============================================

class EventDTO(BaseModel):
    id: UUID
    timestamp: datetime
    event_category: str
    event_outcome: Optional[str]
    event_action: Optional[str]
    source_ip: Optional[str]
    user_name: Optional[str]
    http_method: Optional[str]
    url_path: Optional[str]
    http_response_status_code: Optional[int]
    user_agent: Optional[str]
    log_source: str


class EventListDTO(BaseModel):
    items: List[EventDTO]
    total: int
    page: int
    page_size: int


# ============================================
# Incidents
# ============================================

class IncidentDTO(BaseModel):
    id: UUID
    rule_name: str
    rule_version: Optional[str]
    severity: str
    first_event_at: datetime
    last_event_at: datetime
    detected_at: datetime
    source_ip: Optional[str]
    target_user_name: Optional[str]
    event_count: int
    details: Optional[dict[str, Any]]
    contributing_events: Optional[List[UUID]]
    status: str
    notes: Optional[str]


class IncidentListDTO(BaseModel):
    items: List[IncidentDTO]
    total: int
    page: int
    page_size: int


class IncidentStatusUpdate(BaseModel):
    """Request body for PATCH /incidents/{id}/status."""
    status: str  # one of: open, acknowledged, closed, false_positive
    notes: Optional[str] = None


# ============================================
# Stats
# ============================================

class StatsSummaryDTO(BaseModel):
    """Top-level numbers shown on the Dashboard."""
    events_total: int
    events_last_hour: int
    incidents_open: int
    incidents_total: int
    incidents_by_severity: dict[str, int]   # {"high": 3, "medium": 1, ...}
    incidents_by_rule: dict[str, int]       # {"brute_force": 2, ...}


class TimeBucketDTO(BaseModel):
    bucket: datetime
    event_count: int
    incident_count: int


class StatsTimeseriesDTO(BaseModel):
    interval_seconds: int
    points: List[TimeBucketDTO]


# ============================================
# Rules
# ============================================
# These are read from configuration, not from the database. The Gateway
# exposes them so the frontend can show the operator what is currently
# being detected and at what thresholds.

class RuleDTO(BaseModel):
    name: str
    description: str
    severity: str
    threshold: int
    window_seconds: int