"""
Async PostgreSQL writer for incidents.

The Alert Manager owns the canonical state of every detected attack:
    - finds the most recent OPEN incident matching (rule_name, source_ip)
    - either updates that row (merge) or inserts a fresh one (new attack)

Connection lifecycle mirrors the Normalizer's EventWriter:
    writer = IncidentStore(dsn)
    await writer.connect()
    ...
    await writer.close()
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from sqlalchemy import (
    ARRAY, Column, Integer, MetaData, String, Table, Text, text,
)
from sqlalchemy.dialects.postgresql import (
    INET, JSONB, TIMESTAMP, UUID, insert as pg_insert,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shared.ecs_models import Incident

logger = logging.getLogger(__name__)


# ============================================
# Table definition
# ============================================
# Mirrors migrations/001_initial_schema.sql for the incidents table.

_metadata = MetaData()

incidents_table = Table(
    "incidents",
    _metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("rule_name", String(128), nullable=False),
    Column("rule_version", String(32)),
    Column("severity", String(16), nullable=False),
    Column("first_event_at", TIMESTAMP(timezone=True), nullable=False),
    Column("last_event_at", TIMESTAMP(timezone=True), nullable=False),
    Column("detected_at", TIMESTAMP(timezone=True), nullable=False),
    Column("source_ip", INET),
    Column("target_user_name", String(255)),
    Column("event_count", Integer, nullable=False),
    Column("details", JSONB),
    Column("contributing_events", ARRAY(UUID(as_uuid=True))),
    Column("status", String(32), nullable=False),
    Column("notes", Text),
)


# ============================================
# DSN
# ============================================

def build_dsn(
    user: Optional[str] = None,
    password: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[str] = None,
    database: Optional[str] = None,
) -> str:
    user = user or os.environ["POSTGRES_USER"]
    password = password or os.environ["POSTGRES_PASSWORD"]
    host = host or os.environ.get("POSTGRES_HOST", "postgres")
    port = port or os.environ.get("POSTGRES_PORT", "5432")
    database = database or os.environ["POSTGRES_DB"]
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


# ============================================
# IncidentStore
# ============================================

class IncidentStore:
    """
    Single owner of the engine and connection pool for the Alert Manager.

    Two operations:
        find_open_match  — used by dedup before deciding insert vs update
        upsert           — applies the dedup decision to the database
    """

    def __init__(self, dsn: str, *, pool_size: int = 5, echo: bool = False):
        self._dsn = dsn
        self._engine: Optional[AsyncEngine] = create_async_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=0,
            pool_pre_ping=True,
            echo=echo,
        )

    async def connect(self) -> None:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("IncidentStore connected")

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            logger.info("IncidentStore closed")

    # ----- queries -----

    async def find_open_match(
        self, rule_name: str, source_ip: Optional[str],
    ) -> Optional[Incident]:
        """
        Return the most recent OPEN incident for (rule_name, source_ip),
        or None if no match exists. Powered by idx_incidents_dedup
        (partial composite index from migration 004).
        """
        if self._engine is None:
            raise RuntimeError("not connected")

        # source_ip needs explicit IS NULL handling — INET = NULL never matches
        if source_ip is None:
            query = text(
                "SELECT * FROM incidents "
                "WHERE rule_name = :rule_name "
                "AND source_ip IS NULL "
                "AND status = 'open' "
                "ORDER BY last_event_at DESC LIMIT 1"
            )
            params = {"rule_name": rule_name}
        else:
            query = text(
                "SELECT * FROM incidents "
                "WHERE rule_name = :rule_name "
                "AND source_ip = :source_ip "
                "AND status = 'open' "
                "ORDER BY last_event_at DESC LIMIT 1"
            )
            params = {"rule_name": rule_name, "source_ip": source_ip}

        async with self._engine.begin() as conn:
            row = (await conn.execute(query, params)).mappings().one_or_none()

        if row is None:
            return None
        return self._row_to_incident(dict(row))

    # ----- writes -----

    async def insert(self, incident: Incident) -> None:
        """Persist a fresh incident."""
        if self._engine is None:
            raise RuntimeError("not connected")

        row = self._incident_to_row(incident)
        stmt = pg_insert(incidents_table).values(**row)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def update(self, incident: Incident) -> None:
        """
        Apply a merge: keep id and detected_at; overwrite the rest with
        the merged values. Status is also preserved (an operator may
        have triaged the incident in the meantime — though our flow
        will only ever be merging into OPEN rows by construction).
        """
        if self._engine is None:
            raise RuntimeError("not connected")

        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE incidents SET "
                    "    last_event_at = :last_event_at, "
                    "    event_count = :event_count, "
                    "    details = CAST(:details AS JSONB), "
                    "    contributing_events = :contributing_events "
                    "WHERE id = :id AND status = 'open'"
                ),
                {
                    "id": incident.id,
                    "last_event_at": incident.last_event_at,
                    "event_count": incident.event_count,
                    "details": json.dumps(incident.details or {}),
                    "contributing_events": list(incident.contributing_events or []),
                },
            )

    # ----- helpers -----

    @staticmethod
    def _incident_to_row(incident: Incident) -> dict:
        source_ip = (
            str(incident.source_ip) if incident.source_ip is not None else None
        )
        return {
            "id": incident.id,
            "rule_name": incident.rule_name,
            "rule_version": incident.rule_version,
            "severity": incident.severity,
            "first_event_at": incident.first_event_at,
            "last_event_at": incident.last_event_at,
            "detected_at": incident.detected_at,
            "source_ip": source_ip,
            "target_user_name": incident.target_user_name,
            "event_count": incident.event_count,
            "details": incident.details,
            "contributing_events": list(incident.contributing_events or []),
            "status": incident.status,
            "notes": incident.notes,
        }

    @staticmethod
    def _row_to_incident(row: dict) -> Incident:
        # Postgres asyncpg returns datetime, UUID, etc. as native types,
        # which Pydantic 2 happily accepts.
        return Incident(**row)