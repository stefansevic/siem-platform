"""
API Gateway service.

Read-mostly REST API consumed by the React dashboard. Exposes:

    GET  /health                          — liveness probe
    GET  /stats/summary                   — dashboard top numbers
    GET  /stats/timeseries                — line/bar chart data
    GET  /events                          — paginated event list with filters
    GET  /events/{id}                     — single event detail
    GET  /incidents                       — paginated incident list with filters
    GET  /incidents/{id}                  — single incident detail
    PATCH /incidents/{id}/status          — operator triage
    GET  /rules                           — list of active correlation rules

The service does NOT subscribe to Redis streams; the dashboard uses
HTTP polling (every 5s) which is plenty for a SIEM operator view.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import Settings
from app.db import Database
from app.models import (
    EventDTO,
    EventListDTO,
    IncidentDTO,
    IncidentListDTO,
    IncidentStatusUpdate,
    RuleDTO,
    StatsSummaryDTO,
    StatsTimeseriesDTO,
    TimeBucketDTO,
)
from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import TransportError as ESTransportError


# ============================================
# Logging
# ============================================

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def _configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)


logger = logging.getLogger("api-gateway")


# ============================================
# Lifespan: connect/close DB on startup/shutdown
# ============================================

settings = Settings()
_configure_logging(settings.log_level)

database = Database(settings.postgres_dsn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting api-gateway")
    await database.connect()

    # Open Elasticsearch client. Stored on app.state so request handlers
    # can reach it via dependency injection without globals.
    es = AsyncElasticsearch(
        settings.elasticsearch_url,
        request_timeout=10.0,
        retry_on_timeout=True,
        max_retries=3,
    )
    try:
        info = await es.info()
        logger.info(
            "Connected to Elasticsearch %s",
            info["version"]["number"],
        )
        app.state.es = es
    except Exception as exc:
        logger.warning(
            "Elasticsearch unreachable: %s — /events/search will return 503",
            exc,
        )
        await es.close()
        app.state.es = None

    yield

    logger.info("stopping api-gateway")
    if app.state.es is not None:
        await app.state.es.close()
    await database.close()


app = FastAPI(
    title="SIEM API Gateway",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)


# ============================================
# Health
# ============================================

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ============================================
# Stats — Dashboard
# ============================================

@app.get("/stats/summary", response_model=StatsSummaryDTO)
async def stats_summary() -> StatsSummaryDTO:
    """
    Top-level dashboard numbers. One round-trip per request — small
    SQL surface, fine for 5-second polling.
    """
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    async with database.engine.begin() as conn:
        events_total = (await conn.execute(
            text("SELECT COUNT(*) AS n FROM events")
        )).scalar()

        events_last_hour = (await conn.execute(
            text("SELECT COUNT(*) AS n FROM events WHERE timestamp >= :t"),
            {"t": one_hour_ago},
        )).scalar()

        incidents_total = (await conn.execute(
            text("SELECT COUNT(*) AS n FROM incidents")
        )).scalar()

        incidents_open = (await conn.execute(
            text("SELECT COUNT(*) AS n FROM incidents WHERE status = 'open'")
        )).scalar()

        sev_rows = (await conn.execute(text(
            "SELECT severity, COUNT(*) AS n FROM incidents "
            "WHERE status = 'open' GROUP BY severity"
        ))).all()

        rule_rows = (await conn.execute(text(
            "SELECT rule_name, COUNT(*) AS n FROM incidents "
            "WHERE status = 'open' GROUP BY rule_name"
        ))).all()

    return StatsSummaryDTO(
        events_total=events_total or 0,
        events_last_hour=events_last_hour or 0,
        incidents_total=incidents_total or 0,
        incidents_open=incidents_open or 0,
        incidents_by_severity={row.severity: row.n for row in sev_rows},
        incidents_by_rule={row.rule_name: row.n for row in rule_rows},
    )


@app.get("/stats/timeseries", response_model=StatsTimeseriesDTO)
async def stats_timeseries(
    minutes: int = Query(default=60, ge=5, le=1440),
    interval_seconds: int = Query(default=60, ge=10, le=3600),
) -> StatsTimeseriesDTO:
    """
    Bucketed event and incident counts for the last `minutes` minutes,
    grouped into `interval_seconds` buckets. Used by the Dashboard line
    chart.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)

    # Postgres date_trunc would only get us minutes; for arbitrary
    # bucket sizes we use to_timestamp(floor(epoch / interval) * interval).
    sql = text("""
        WITH event_buckets AS (
            SELECT to_timestamp(
                floor(extract(epoch FROM timestamp) / :interval)
                * :interval
            ) AT TIME ZONE 'UTC' AS bucket,
            COUNT(*) AS n
            FROM events
            WHERE timestamp >= :start
            GROUP BY bucket
        ),
        incident_buckets AS (
            SELECT to_timestamp(
                floor(extract(epoch FROM detected_at) / :interval)
                * :interval
            ) AT TIME ZONE 'UTC' AS bucket,
            COUNT(*) AS n
            FROM incidents
            WHERE detected_at >= :start
            GROUP BY bucket
        )
        SELECT
            COALESCE(e.bucket, i.bucket) AS bucket,
            COALESCE(e.n, 0) AS events,
            COALESCE(i.n, 0) AS incidents
        FROM event_buckets e
        FULL OUTER JOIN incident_buckets i ON e.bucket = i.bucket
        ORDER BY bucket
    """)

    async with database.engine.begin() as conn:
        rows = (await conn.execute(sql, {
            "interval": interval_seconds,
            "start": start,
        })).all()

    return StatsTimeseriesDTO(
        interval_seconds=interval_seconds,
        points=[
            TimeBucketDTO(
                bucket=row.bucket,
                event_count=row.events,
                incident_count=row.incidents,
            )
            for row in rows
        ],
    )


# ============================================
# Events
# ============================================

@app.get("/events", response_model=EventListDTO)
async def list_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    source_ip: Optional[str] = None,
    event_category: Optional[str] = None,
    event_outcome: Optional[str] = None,
    user_name: Optional[str] = None,
    status_code: Optional[int] = None,
    log_source: Optional[str] = None,
    since_minutes: Optional[int] = Query(default=None, ge=1, le=10080),
) -> EventListDTO:
    """
    Paginated event list. All filters are optional and combine with AND.
    """
    where_parts: List[str] = []
    params: dict = {}

    if source_ip:
        where_parts.append("source_ip = :source_ip")
        params["source_ip"] = source_ip
    if event_category:
        where_parts.append("event_category = :event_category")
        params["event_category"] = event_category
    if event_outcome:
        where_parts.append("event_outcome = :event_outcome")
        params["event_outcome"] = event_outcome
    if user_name:
        where_parts.append("user_name = :user_name")
        params["user_name"] = user_name
    if status_code is not None:
        where_parts.append("http_response_status_code = :status_code")
        params["status_code"] = status_code
    if log_source:
        where_parts.append("log_source = :log_source")
        params["log_source"] = log_source
    if since_minutes is not None:
        where_parts.append("timestamp >= :since")
        params["since"] = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    async with database.engine.begin() as conn:
        total = (await conn.execute(
            text(f"SELECT COUNT(*) AS n FROM events {where_clause}"),
            params,
        )).scalar() or 0

        rows = (await conn.execute(text(f"""
            SELECT id, timestamp, event_category, event_outcome, event_action,
                   host(source_ip) AS source_ip, user_name,
                   http_method, url_path, http_response_status_code,
                   user_agent, log_source
            FROM events
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT :limit OFFSET :offset
        """), params)).mappings().all()

    return EventListDTO(
        items=[EventDTO(**dict(row)) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ============================================
# Events search (Elasticsearch-backed)
# ============================================

@app.get("/events/search", response_model=EventListDTO)
async def search_events(
    q: Optional[str] = Query(
        None,
        description="Free-text search across user_name, url_path, and user_agent.",
    ),
    source_ip: Optional[str] = Query(None),
    user_name: Optional[str] = Query(None),
    event_outcome: Optional[str] = Query(None, pattern="^(success|failure)$"),
    http_method: Optional[str] = Query(None),
    status_min: Optional[int] = Query(None, ge=100, le=599),
    status_max: Optional[int] = Query(None, ge=100, le=599),
    since: Optional[datetime] = Query(
        None,
        description="ISO-8601 timestamp; only events at or after this time.",
    ),
    until: Optional[datetime] = Query(
        None,
        description="ISO-8601 timestamp; only events before this time.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> EventListDTO:
    """
    Full-text and structured search over the events index family.

    Routes through Elasticsearch rather than Postgres because ES is
    purpose-built for this workload (low-latency keyword filters,
    full-text matching, time-range aggregations). Postgres remains the
    transactional source for incident workflow.

    Returns an empty page if Elasticsearch is unreachable, with a
    warning logged — degrades gracefully instead of returning 500.
    """
    es: Optional[AsyncElasticsearch] = app.state.es
    if es is None:
        logger.warning("ES unavailable; /events/search returning empty result")
        return EventListDTO(items=[], total=0, page=page, page_size=page_size)

    # Build the ES query body. Each filter is appended only if the
    # caller supplied it, so an empty request matches everything.
    filters: List[dict] = []
    must: List[dict] = []

    if source_ip:
        filters.append({"term": {"source_ip": source_ip}})
    if user_name:
        filters.append({"term": {"user_name": user_name}})
    if event_outcome:
        filters.append({"term": {"event_outcome": event_outcome}})
    if http_method:
        filters.append({"term": {"http_method": http_method.upper()}})

    if status_min is not None or status_max is not None:
        rng: dict = {}
        if status_min is not None:
            rng["gte"] = status_min
        if status_max is not None:
            rng["lte"] = status_max
        filters.append({"range": {"http_response_status_code": rng}})

    if since is not None or until is not None:
        time_range: dict = {}
        if since is not None:
            time_range["gte"] = since.isoformat()
        if until is not None:
            time_range["lt"] = until.isoformat()
        filters.append({"range": {"timestamp": time_range}})

    if q:
        # Free text matches against multiple fields. user_name and
        # url_path are searched as both keyword (exact) and text
        # (partial) thanks to the multi-field mapping in the template.
        must.append({
            "multi_match": {
                "query": q,
                "fields": ["user_name^2", "url_path", "user_agent"],
                "type": "best_fields",
            }
        })

    body = {
        "query": {
            "bool": {
                "must": must if must else [{"match_all": {}}],
                "filter": filters,
            }
        },
        "sort": [{"timestamp": {"order": "desc"}}],
        "from": (page - 1) * page_size,
        "size": page_size,
        "track_total_hits": True,
    }

    try:
        response = await es.search(index="events-*", body=body)
    except ESTransportError as exc:
        logger.warning("ES search failed: %s", exc)
        return EventListDTO(items=[], total=0, page=page, page_size=page_size)

    total = response["hits"]["total"]["value"]
    items: List[EventDTO] = []
    for hit in response["hits"]["hits"]:
        src = hit["_source"]
        items.append(EventDTO(
            id=UUID(src["event_id"]),
            timestamp=datetime.fromisoformat(src["timestamp"]),
            event_category=src.get("event_category", ""),
            event_outcome=src.get("event_outcome"),
            event_action=src.get("event_action"),
            source_ip=src.get("source_ip"),
            user_name=src.get("user_name"),
            http_method=src.get("http_method"),
            url_path=src.get("url_path"),
            http_response_status_code=src.get("http_response_status_code"),
            user_agent=src.get("user_agent"),
            log_source=src.get("log_source", "unknown"),
        ))

    return EventListDTO(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/events/{event_id}", response_model=EventDTO)
async def get_event(event_id: UUID) -> EventDTO:
    async with database.engine.begin() as conn:
        row = (await conn.execute(text("""
            SELECT id, timestamp, event_category, event_outcome, event_action,
                   host(source_ip) AS source_ip, user_name,
                   http_method, url_path, http_response_status_code,
                   user_agent, log_source
            FROM events WHERE id = :id
        """), {"id": event_id})).mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="event not found")
    return EventDTO(**dict(row))


# ============================================
# Incidents
# ============================================

_VALID_STATUSES = {"open", "acknowledged", "closed", "false_positive"}


@app.get("/incidents", response_model=IncidentListDTO)
async def list_incidents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    status: Optional[str] = None,
    severity: Optional[str] = None,
    rule_name: Optional[str] = None,
    source_ip: Optional[str] = None,
    since_minutes: Optional[int] = Query(default=None, ge=1, le=10080),
    detected_after: Optional[datetime] = None,
    detected_before: Optional[datetime] = None,
) -> IncidentListDTO:
    where_parts: List[str] = []
    params: dict = {}

    if status:
        where_parts.append("status = :status")
        params["status"] = status
    if severity:
        where_parts.append("severity = :severity")
        params["severity"] = severity
    if rule_name:
        where_parts.append("rule_name = :rule_name")
        params["rule_name"] = rule_name
    if source_ip:
        where_parts.append("source_ip = :source_ip")
        params["source_ip"] = source_ip
    if since_minutes is not None:
        where_parts.append("detected_at >= :since")
        params["since"] = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    if detected_after is not None:
        where_parts.append("detected_at >= :detected_after")
        params["detected_after"] = detected_after
    if detected_before is not None:
        where_parts.append("detected_at <= :detected_before")
        params["detected_before"] = detected_before

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    async with database.engine.begin() as conn:
        total = (await conn.execute(
            text(f"SELECT COUNT(*) AS n FROM incidents {where_clause}"),
            params,
        )).scalar() or 0

        rows = (await conn.execute(text(f"""
            SELECT id, rule_name, rule_version, severity,
                   first_event_at, last_event_at, detected_at,
                   host(source_ip) AS source_ip, target_user_name,
                   event_count, details, contributing_events,
                   status, notes
            FROM incidents
            {where_clause}
            ORDER BY detected_at DESC
            LIMIT :limit OFFSET :offset
        """), params)).mappings().all()

    return IncidentListDTO(
        items=[IncidentDTO(**dict(row)) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/incidents/{incident_id}", response_model=IncidentDTO)
async def get_incident(incident_id: UUID) -> IncidentDTO:
    async with database.engine.begin() as conn:
        row = (await conn.execute(text("""
            SELECT id, rule_name, rule_version, severity,
                   first_event_at, last_event_at, detected_at,
                   host(source_ip) AS source_ip, target_user_name,
                   event_count, details, contributing_events,
                   status, notes
            FROM incidents WHERE id = :id
        """), {"id": incident_id})).mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return IncidentDTO(**dict(row))


@app.patch("/incidents/{incident_id}/status", response_model=IncidentDTO)
async def update_incident_status(
    incident_id: UUID,
    body: IncidentStatusUpdate,
) -> IncidentDTO:
    """Operator triage: change status (and optionally append notes)."""
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {sorted(_VALID_STATUSES)}",
        )

    async with database.engine.begin() as conn:
        result = await conn.execute(text("""
            UPDATE incidents
            SET status = :status,
                notes = COALESCE(:notes, notes)
            WHERE id = :id
            RETURNING id
        """), {
            "id": incident_id,
            "status": body.status,
            "notes": body.notes,
        })
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="incident not found")

    return await get_incident(incident_id)


# ============================================
# Rules
# ============================================

@app.get("/rules", response_model=List[RuleDTO])
async def list_rules() -> List[RuleDTO]:
    """
    Return the active correlation rules and their thresholds.

    These mirror the defaults from services/correlator/app/config.py.
    Long-term they should be discovered from the Correlator service
    over a control-plane endpoint, but for the prototype we hard-code
    them here so the dashboard always has a reference.
    """
    return [
        RuleDTO(
            name="brute_force",
            description="Multiple failed authentication attempts from the same IP within a short window.",
            severity="high",
            threshold=5,
            window_seconds=60,
        ),
        RuleDTO(
            name="directory_scanning",
            description="Many distinct 404 paths probed from the same IP.",
            severity="medium",
            threshold=20,
            window_seconds=60,
        ),
        RuleDTO(
            name="account_takeover",
            description="A series of failed logins followed by a successful one from the same IP.",
            severity="critical",
            threshold=5,
            window_seconds=600,
        ),
    ]