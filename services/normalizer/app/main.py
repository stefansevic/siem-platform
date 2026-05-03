"""
Normalizer service entry point.

Lifecycle:
    1. Parse Settings (fails fast if env is incomplete).
    2. Configure JSON logging.
    3. Connect EventWriter to Postgres.
    4. Connect NormalizerConsumer to Redis (creates group if needed).
    5. Run the consumer loop until SIGINT/SIGTERM.
    6. Stop loop, close Redis and Postgres in reverse order.

The service does NOT expose HTTP; it is a pure background worker.
Health is observable via Postgres/Redis connectivity and stream lag.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from app.config import Settings
from app.db import EventWriter
from app.redis_consumer import NormalizerConsumer


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


async def run_service() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("normalizer")
    logger.info("starting normalizer")

    writer = EventWriter(
        settings.postgres_dsn,
        elasticsearch_url=settings.elasticsearch_url,
        elasticsearch_required=settings.elasticsearch_required,
    )
    await writer.connect()

    # Apply the index template once ES is reachable. Idempotent:
    # subsequent restarts do nothing if the template already exists.
    if writer._es is not None:
        from shared.elasticsearch_index import (
            TEMPLATE_NAME,
            build_template_body,
        )
        try:
            await writer._es.indices.put_index_template(
                name=TEMPLATE_NAME,
                **build_template_body(),
            )
            logger.info("Index template applied: %s", TEMPLATE_NAME)
        except Exception as exc:
            logger.warning("Failed to apply index template: %s", exc)

    consumer = NormalizerConsumer(
        redis_url=settings.redis_url,
        writer=writer,
    )
    await consumer.connect()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, consumer.stop)

    try:
        await consumer.run()
    finally:
        logger.info("stopping normalizer")
        await consumer.close()
        await writer.close()
        logger.info("normalizer stopped cleanly")


def main() -> None:
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
