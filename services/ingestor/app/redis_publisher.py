"""
Async Redis publisher za sirove log poruke.

Greške se loguju, ali se nikad ne dižu: gubitak jednog loga ne sme
da sruši ingestor niti da blokira ostale.
"""

import json
import logging
from typing import Optional

import redis.asyncio as redis_async

from shared.ecs_models import RawLogMessage
from shared.redis_keys import STREAM_RAW_LOGS

from .config import settings

logger = logging.getLogger(__name__)


class RedisPublisher:
    """Objavljuje poruke u Redis Streams."""

    def __init__(self) -> None:
        self._client: Optional[redis_async.Redis] = None

    async def connect(self) -> None:
        """Otvori Redis konekciju. Poziva se jednom, na startu aplikacije."""
        self._client = redis_async.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        # Ping odmah, da rano otkrijemo da li veza radi
        try:
            await self._client.ping()
            logger.info(
                "redis_connected",
                extra={"event_data": {"host": settings.redis_host, "port": settings.redis_port}},
            )
        except Exception:
            logger.exception("redis_ping_failed")
            raise

    async def disconnect(self) -> None:
        """Zatvori Redis konekciju pri gašenju aplikacije."""
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("redis_disconnected")

    async def publish_raw_log(self, message: RawLogMessage) -> Optional[str]:
        """
        Objavi RawLogMessage u `raw_logs` stream.

        Vraća ID stream unosa koji Redis dodeli, ili None ako padne.
        Greške se loguju, nikad ne dižu.
        """
        if self._client is None:
            logger.error("publish_called_before_connect")
            return None

        # Redis Streams očekuje ravan dict stringova. Celu poruku
        # serijalizujemo kao JSON, da je Normalizer sklopi nazad jednim pozivom.
        payload = {"data": message.model_dump_json()}

        try:
            entry_id = await self._client.xadd(STREAM_RAW_LOGS, payload)
            return entry_id
        except Exception:
            logger.exception(
                "redis_publish_failed",
                extra={"event_data": {"stream": STREAM_RAW_LOGS, "source": message.source}},
            )
            return None