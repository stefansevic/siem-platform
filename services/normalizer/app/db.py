"""
Async PostgreSQL upisivač normalizovanih događaja.

Koristi SQLAlchemy Core (ne ORM) sa asyncpg drajverom.

Idempotency:
    Svaki insert ide uz ON CONFLICT (idempotency_key) DO NOTHING.
    Duplirani upisi postaju tihi, pa je exactly-once očuvan.

Životni ciklus konekcije:
    Normalizer napravi jedan EventWriter na startu, pozove connect() i
    koristi ga tokom celog rada. close() se poziva jednom, pri gašenju.
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
# Definicija tabele
# ============================================


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
# sastavi connection string (DSN)
# ============================================

def build_dsn(
    user: Optional[str] = None,
    password: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[str] = None,
    database: Optional[str] = None,
) -> str:
    """
    Sastavlja async PostgreSQL DSN iz prosleđenih argumenata ili iz
    POSTGRES_* environment varijabli.
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
    Jedini vlasnik async engine-a i pool-a konekcija.

    Upotreba:
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
            pool_pre_ping=True,  # jeftina provera da je konekcija živa pre korišćenja
            echo=echo,
        )
        # ES je opcion. Ako je nedostupan, dual-write se svede na upis
        # samo u Postgres; Postgres je source of truth, pa SIEM nastavlja
        # da detektuje incidente i bez ES-a.
        self._es_url = elasticsearch_url
        self._es_required = elasticsearch_required
        self._es: Optional[AsyncElasticsearch] = None

    async def connect(self) -> None:
        """Proveri konekciju sa SELECT 1, pa se opciono poveži i na
        Elasticsearch za dual-write."""
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
                # Odbaci klijent, da insert_event preskoči upis u ES
                await self._es.close()
                self._es = None

    async def close(self) -> None:
        """Ugasi engine, njegov pool i ES klijent."""
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
        Upisuje jedan ECSEvent. Vraća True ako je upisan nov red,
        False ako red sa istim idempotency_key već postoji.

        Na uspešan Postgres upis (tj. događaj nije duplikat), isti
        događaj se indeksira i u Elasticsearch. ES greške se loguju,
        ali ne dižu izuzetak - Postgres ostaje source of truth.
        """
        if self._engine is None:
            raise RuntimeError("EventWriter is not connected")

        row = self._event_to_row(event, idempotency_key)
        stmt = pg_insert(events_table).values(**row)
        stmt = stmt.on_conflict_do_nothing(index_elements=["idempotency_key"])

        async with self._engine.begin() as conn:  # type: AsyncConnection
            result = await conn.execute(stmt)
            inserted = result.rowcount == 1

        # Dual-write u ES samo za nove događaje. Duplikate preskačemo
        # jer su već u ES-u od prvog upisa.
        if inserted and self._es is not None:
            await self._index_event_in_es(event)

        return inserted

    async def _index_event_in_es(self, event: ECSEvent) -> None:
        """
        Šalje događaj u dnevni ES indeks. Greške se gutaju uz warning -
        ES je best-effort, Postgres je source of truth.
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
        # Izbaci None vrednosti, da ES ne čuva eksplicitne null-ove
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
        Pretvara ECSEvent u običan dict spreman za SQLAlchemy.

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