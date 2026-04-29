"""
Async PostgreSQL connection for the API Gateway.

This service is read-mostly (it only writes when an operator changes
incident status). One async engine, owned by the FastAPI app, reused
for the lifetime of the process.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)


class Database:
    """Thin wrapper around the async engine for lifecycle management."""

    def __init__(self, dsn: str, *, pool_size: int = 5):
        self._dsn = dsn
        self._engine: Optional[AsyncEngine] = create_async_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=0,
            pool_pre_ping=True,
        )

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database is not connected")
        return self._engine

    async def connect(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connected")

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            logger.info("Database closed")