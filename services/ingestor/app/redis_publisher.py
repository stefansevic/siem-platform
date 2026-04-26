"""
Async Redis publisher for raw log messages.

Wraps the redis-py async client and exposes a single high-level method:
    await publisher.publish_raw_log(message)

Failures are logged but never raised - losing one log entry must not
crash the ingestor or block other producers.
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
    """Async Redis Streams publisher."""

    def __init__(self) -> None:
        self._client: Optional[redis_async.Redis] = None

    async def connect(self) -> None:
        """Open the Redis connection. Called once at app startup."""
        self._client = redis_async.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        # Ping to verify connectivity early
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
        """Close the Redis connection at app shutdown."""
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("redis_disconnected")

    async def publish_raw_log(self, message: RawLogMessage) -> Optional[str]:
        """
        Publish a RawLogMessage to the `raw_logs` stream.

        Returns the Redis-assigned stream entry ID on success, or None on
        failure. Errors are logged but never raised.
        """
        if self._client is None:
            logger.error("publish_called_before_connect")
            return None

        # Redis Streams expect a flat dict of strings.
        # We serialize the entire message as JSON so the consumer
        # (Normalizer) can rebuild it with one call.
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