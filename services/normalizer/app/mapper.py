"""
Prevodi ParsedFields u ECSEvent
(kanonska šema koja se čuva u events tabeli).

Ovde je logika kako se događaji kategorizuju i kako se ishod zaključuje
kad ga izvor ne kaže eksplicitno (npr. Nginx access log ne zna za
uspeh/neuspeh, pa ga izvodimo iz HTTP status koda).

Kao i parseri, ovaj modul je bez I/O.
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
    """Diže se kad parsiran događaj ne može da postane ECSEvent."""


class SkipEvent(Exception):
    """
    Diže se kad događaj namerno NE čuvamo.

    Primeri: webapp lifecycle događaji, šum od healthcheck-a.
    Normalizer to tretira kao ne-grešku: samo potvrdi poruku i idi dalje.
    """


def normalize(raw: RawLogMessage) -> ECSEvent:

#redis_consumer poziva ovu funkciju za svaki sirovi log koji je Ingestor poslao.

    fields = _dispatch_parser(raw) # sirovo -> ParsedFields
    return _to_ecs(fields, raw) # ParsedFields -> ECSEvent




# ============================================
# Internals
# ============================================

def _dispatch_parser(raw: RawLogMessage) -> ParsedFields:
    """
    Bira parser prema oznaci formata koju je Ingestor zakačio.
    nginx_combined ili json.

    """
    
    fmt = raw.format

    if fmt == LogFormat.NGINX_COMBINED.value or fmt == LogFormat.NGINX_COMBINED:
        return parse_nginx_siem_combined(raw.payload)
    if fmt == LogFormat.JSON.value or fmt == LogFormat.JSON:
        return parse_demo_webapp_json(raw.payload)
    raise ParseError(f"no parser for format: {fmt!r}")


def _to_ecs(fields: ParsedFields, raw: RawLogMessage) -> ECSEvent:
    """
    Mapira ParsedFields u ECSEvent.

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
        event_action=fields.event_type,  
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
    Mapira event_type u ECS event.category. authentication | authorization | web

    Lifecycle (app se pokrenula, app se gasi,..) događaji se filtriraju ranije - dovde ne bi trebalo da stignu.
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

    # Nepoznat / nedostajući event_type ide na web.
    return EventCategory.WEB


def _derive_outcome(fields: ParsedFields) -> Optional[EventOutcome]:
    """
    Određuje event.outcome. (success | failure | None)

    """
    # 1. Eksplicitan ishod (webapp)
    if fields.outcome == "success":
        return EventOutcome.SUCCESS
    if fields.outcome == "failure":
        return EventOutcome.FAILURE

    # 2. Heuristika po HTTP status kodu (nginx)
    code = fields.http_status
    if code is not None:
        if 200 <= code < 400:
            return EventOutcome.SUCCESS
        if 400 <= code < 600:
            return EventOutcome.FAILURE

    # 3. Nemamo se na šta osloniti
    return None


def _coerce_log_source(value) -> LogSource:
    """
    Pretvara raw.source u LogSource enum.
    """
    if isinstance(value, LogSource):
        return value
    try:
        return LogSource(value)
    except ValueError:
        return LogSource.UNKNOWN