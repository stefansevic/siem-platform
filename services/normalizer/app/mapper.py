"""
Maps ParsedFields (source-neutral parser output) to ECSEvent (canonical
schema persisted in the events table).

This module owns the business logic of how raw events are categorized
and how outcomes are inferred when the source does not state them
explicitly (e.g., Nginx access logs have no notion of success/failure
but we derive it from the HTTP status code).

Like parsers, this module is I/O-free.
"""

from __future__ import annotations

from typing import Optional

from shared.ecs_models import (
    ECSEvent,
    EventCategory,
    EventOutcome,
    LogFormat,
    LogSource,
    RawLogMessage,
)

from app.parsers import (
    ParsedFields,
    ParseError,
    parse_demo_webapp_json,
    parse_nginx_siem_combined,
)


# ============================================
# Public API
# ============================================

class MappingError(ValueError):
    """Raised when a parsed event cannot be turned into an ECSEvent."""


class SkipEvent(Exception):
    """
    Raised when an event is intentionally not persisted.

    Examples: webapp lifecycle events, healthcheck noise.
    The Normalizer treats this as a non-error: ack the message and move on.
    """


def normalize(raw: RawLogMessage) -> ECSEvent:
    """
    Full pipeline: raw payload (string) -> parsed fields -> ECSEvent.

    Raises:
        ParseError: payload is malformed for its declared format.
        SkipEvent:  parsed correctly but should not be persisted.
        MappingError: parsed but mapping rules cannot produce an ECSEvent.
    """
    fields = _dispatch_parser(raw)
    return _to_ecs(fields, raw)


# ============================================
# Internals
# ============================================

def _dispatch_parser(raw: RawLogMessage) -> ParsedFields:
    """Pick a parser based on the format hint attached by the Ingestor."""
    fmt = raw.format
    # `use_enum_values=True` on RawLogMessage means raw.format is a string
    # at runtime ("nginx_combined", "json"), not a LogFormat instance.
    if fmt == LogFormat.NGINX_COMBINED.value or fmt == LogFormat.NGINX_COMBINED:
        return parse_nginx_siem_combined(raw.payload)
    if fmt == LogFormat.JSON.value or fmt == LogFormat.JSON:
        return parse_demo_webapp_json(raw.payload)
    raise ParseError(f"no parser for format: {fmt!r}")


def _to_ecs(fields: ParsedFields, raw: RawLogMessage) -> ECSEvent:
    """
    Apply mapping rules to produce an ECSEvent.

    Decides:
        - event_category from event_type (and source)
        - event_outcome (uses parsed outcome, falls back to status code)
        - log_source from raw.source
    """
    if fields.timestamp is None:
        raise MappingError("ParsedFields missing required timestamp")

    category = _derive_category(fields)
    outcome = _derive_outcome(fields)
    log_source = _coerce_log_source(raw.source)

    return ECSEvent(
        timestamp=fields.timestamp,
        event_category=category,
        event_outcome=outcome,
        event_action=fields.event_type,  # Keep original verb as ECS event.action
        source_ip=fields.source_ip,
        user_name=fields.user_name,
        http_method=fields.http_method,
        url_path=fields.url_path,
        http_response_status_code=fields.http_status,
        user_agent=fields.user_agent,
        log_source=log_source,
        raw_message=raw.payload,
    )


def _derive_category(fields: ParsedFields) -> EventCategory:
    """
    Map event_type to ECS event.category.

    Webapp event_types map directly. Nginx (and other unstructured sources)
    always fall to 'web'.

    Lifecycle events are filtered upstream — they should never reach here.
    """
    et = fields.event_type

    if et == "lifecycle":
        raise SkipEvent("lifecycle events are not persisted")

    if et == "authentication":
        return EventCategory.AUTHENTICATION
    if et == "authorization":
        return EventCategory.AUTHORIZATION
    if et == "http_request":
        return EventCategory.WEB

    # Unknown / missing event_type defaults to web — better than dropping.
    return EventCategory.WEB


def _derive_outcome(fields: ParsedFields) -> Optional[EventOutcome]:
    """
    Determine event.outcome.

    Priority:
      1. Explicit outcome from the source (webapp authentication events)
      2. HTTP status code heuristic (2xx/3xx success, 4xx/5xx failure)
      3. Unknown if neither is available
    """
    # 1. Explicit outcome
    if fields.outcome == "success":
        return EventOutcome.SUCCESS
    if fields.outcome == "failure":
        return EventOutcome.FAILURE

    # 2. HTTP status code heuristic
    code = fields.http_status
    if code is not None:
        if 200 <= code < 400:
            return EventOutcome.SUCCESS
        if 400 <= code < 600:
            return EventOutcome.FAILURE

    # 3. Nothing to go on
    return None


def _coerce_log_source(value) -> LogSource:
    """
    Convert raw.source (string when use_enum_values=True, enum otherwise)
    into a proper LogSource enum.
    """
    if isinstance(value, LogSource):
        return value
    try:
        return LogSource(value)
    except ValueError:
        return LogSource.UNKNOWN