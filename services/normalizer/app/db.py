"""
Async PostgreSQL writer for normalized events.

Uses SQLAlchemy 2.x Core (not the ORM) with the asyncpg driver. The
Core API maps closely to the SQL we want to emit and avoids ORM
overhead for what is essentially a write-only path.

Idempotency:
    Every insert is paired with ON CONFLICT (idempotency_key) DO NOTHING.
    Duplicate ingestions become silent no-ops, preserving exactly-once
    semantics at the storage layer.

Connection lifecycle:
    The Normalizer creates one EventWriter at startup, calls connect(),
    and uses it for the lifetime of the process. close() is called once
    on shutdown.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import (
    Column, Integer, MetaData, String, Table, Text, text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP, UUID, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine
from shared.ecs_models import ECSEvent
from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import TransportError as ESTransportError
from shared.elasticsearch_index import daily_index_name


logger = logging.getLogger(__name__)


# ============================================
# Table definition
# ============================================
# Mirror of migrations/001_initial_schema.sql + 002_add_idempotency_key.sql.
# Kept in sync manually -- this is acceptable for a prototype; in a larger
# project we'd autogenerate from the database via Alembic reflection.

_metadata = MetaData()

events_table = Table(
    "events",
    _metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("timestamp", TIMESTAMP(timezone=True), nullable=False),
    Column("event_category", String(64), nullable=False),
    Column("event_outcome", String(32)),
    Column("event_action", String(64)),
    Column("source_ip", INET),
    Column("source_port", Integer),
    Column("user_name", String(255)),
    Column("http_method", String(16)),
    Column("url_path", Text),
    Column("http_response_status_code", Integer),
    Column("user_agent", Text),
    Column("log_source", String(64), nullable=False),
    Column("raw_message", Text),
    Column("received_at", TIMESTAMP(timezone=True), nullable=False),
    Column("idempotency_key", String(64)),
)


# ============================================
# DSN construction
# ============================================

def build_dsn(
    user: Optional[str] = None,
    password: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[str] = None,
    database: Optional[str] = None,
) -> str:
    """
    Construct an async PostgreSQL DSN from explicit args or the
    POSTGRES_* environment variables.
    """
    user = user or os.environ["POSTGRES_USER"]
    password = password or os.environ["POSTGRES_PASSWORD"]
    host = host or os.environ.get("POSTGRES_HOST", "postgres")
    port = port or os.environ.get("POSTGRES_PORT", "5432")
    database = database or os.environ["POSTGRES_DB"]
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


# ============================================
# EventWriter
# ============================================

class EventWriter:
    """
    Single owner of the async engine and connection pool.

    Usage:
        writer = EventWriter(dsn)
        await writer.connect()
        await writer.insert_event(event, idempotency_key="...")
        ...
        await writer.close()
    """

    def __init__(
        self,
        dsn: str,
        *,
        pool_size: int = 5,
        echo: bool = False,
        elasticsearch_url: Optional[str] = None,
        elasticsearch_required: bool = False,
    ):
        self._dsn = dsn
        self._engine: Optional[AsyncEngine] = create_async_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=0,
            pool_pre_ping=True,  # cheap liveness check before checkout
            echo=echo,
        )
        # ES is optional. If unreachable, dual-write degrades to
        # Postgres-only writes; Postgres is the source of truth so the
        # SIEM keeps detecting incidents even without ES.
        self._es_url = elasticsearch_url
        self._es_required = elasticsearch_required
        self._es: Optional[AsyncElasticsearch] = None

    async def connect(self) -> None:
        """Verify connectivity by issuing SELECT 1, then optionally
        connect to Elasticsearch for dual-write."""
        assert self._engine is not None
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("EventWriter connected to Postgres")

        if self._es_url:
            self._es = AsyncElasticsearch(
                self._es_url,
                request_timeout=10.0,
                retry_on_timeout=True,
                max_retries=3,
            )
            try:
                info = await self._es.info()
                logger.info(
                    "EventWriter connected to Elasticsearch %s",
                    info["version"]["number"],
                )
            except Exception as exc:
                logger.warning("Elasticsearch unreachable: %s", exc)
                if self._es_required:
                    raise
                # Drop the client so insert_event skips ES writes
                await self._es.close()
                self._es = None

    async def close(self) -> None:
        """Dispose the engine, its pool, and the ES client."""
        if self._es is not None:
            await self._es.close()
            self._es = None
            logger.info("Elasticsearch client closed")
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            logger.info("EventWriter closed")

    async def insert_event(
        self,
        event: ECSEvent,
        idempotency_key: str,
    ) -> bool:
        """
        Insert one ECSEvent. Returns True if a new row was written,
        False if a row with the same idempotency_key already exists.

        On a successful Postgres insert (i.e. the event is not a
        duplicate), the same event is also indexed in Elasticsearch.
        ES failures are logged but do not raise — Postgres remains
        the source of truth.
        """
        if self._engine is None:
            raise RuntimeError("EventWriter is not connected")

        row = self._event_to_row(event, idempotency_key)
        stmt = pg_insert(events_table).values(**row)
        stmt = stmt.on_conflict_do_nothing(index_elements=["idempotency_key"])

        async with self._engine.begin() as conn:  # type: AsyncConnection
            result = await conn.execute(stmt)
            inserted = result.rowcount == 1

        # Dual-write to ES on new events only. Duplicates are skipped
        # because they will already be in ES from the original write.
        if inserted and self._es is not None:
            await self._index_event_in_es(event)

        return inserted

    async def _index_event_in_es(self, event: ECSEvent) -> None:
        """
        Send the event to the daily ES index. Failures are swallowed
        with a warning — ES is best-effort, Postgres is the truth.
        """
        index = daily_index_name(event.timestamp)
        document = {
            "event_id": str(event.id),
            "timestamp": event.timestamp.isoformat(),
            "event_kind": getattr(event, "event_kind", None),
            "event_category": event.event_category,
            "event_action": event.event_action,
            "event_outcome": event.event_outcome,
            "source_ip": str(event.source_ip) if event.source_ip else None,
            "user_name": event.user_name,
            "user_agent": getattr(event, "user_agent", None),
            "http_method": event.http_method,
            "url_path": getattr(event, "url_path", None),
            "http_response_status_code": getattr(
                event, "http_response_status_code", None,
            ),
            "log_source": getattr(event, "log_source", None),
            "host_name": getattr(event, "host_name", None),
            "ingested_at": getattr(event, "ingested_at", event.timestamp).isoformat(),
        }
        # Drop None values so ES does not store explicit nulls
        document = {k: v for k, v in document.items() if v is not None}

        try:
            await self._es.index(
                index=index,
                id=str(event.id),
                document=document,
            )
        except ESTransportError as exc:
            logger.warning(
                "Failed to index event %s in Elasticsearch: %s",
                event.id, exc,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected error indexing event %s: %s",
                event.id, exc,
            )

    @staticmethod
    def _event_to_row(event: ECSEvent, idempotency_key: str) -> dict:
        """
        Convert an ECSEvent to a plain dict ready for SQLAlchemy.

        Pydantic exposes IPvAnyAddress as a non-string type that asyncpg
        cannot bind directly, so we coerce it.
        """
        source_ip = str(event.source_ip) if event.source_ip is not None else None

        return {
            "id": event.id,
            "timestamp": event.timestamp,
            "event_category": event.event_category,
            "event_outcome": event.event_outcome,
            "event_action": event.event_action,
            "source_ip": source_ip,
            "source_port": event.source_port,
            "user_name": event.user_name,
            "http_method": event.http_method,
            "url_path": event.url_path,
            "http_response_status_code": event.http_response_status_code,
            "user_agent": event.user_agent,
            "log_source": event.log_source,
            "raw_message": event.raw_message,
            "received_at": event.received_at,
            "idempotency_key": idempotency_key,
        }