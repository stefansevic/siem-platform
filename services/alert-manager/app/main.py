"""
Alert Manager service entry point.

Lifecycle:
    1. Parse Settings.
    2. Configure JSON logging.
    3. Build the notifier composition (console always; webhook if URL set).
    4. Connect IncidentStore to Postgres.
    5. Connect AlertManagerConsumer to Redis.
    6. Run the read loop until SIGINT/SIGTERM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone
from typing import List

from app.config import Settings
from app.db import IncidentStore
from app.notifiers import (
    CompositeNotifier,
    ConsoleNotifier,
    Notifier,
    WebhookNotifier,
)
from app.redis_consumer import AlertManagerConsumer


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


def _build_notifier(settings: Settings, logger: logging.Logger) -> Notifier:
    notifiers: List[Notifier] = [ConsoleNotifier()]

    if settings.webhook_url:
        logger.info("webhook configured: %s", settings.webhook_url)
        notifiers.append(WebhookNotifier(
            settings.webhook_url,
            timeout_seconds=settings.webhook_timeout_seconds,
        ))
    else:
        logger.info("webhook not configured (set ALERT_WEBHOOK_URL to enable)")

    return CompositeNotifier(notifiers)


async def run_service() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("alert-manager")
    logger.info("starting alert-manager")

    notifier = _build_notifier(settings, logger)

    store = IncidentStore(settings.postgres_dsn)
    await store.connect()

    consumer = AlertManagerConsumer(
        redis_url=settings.redis_url,
        store=store,
        notifier=notifier,
        silence_window=timedelta(seconds=settings.silence_window_seconds),
    )
    await consumer.connect()
    logger.info(
        "silence window: %ds", settings.silence_window_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, consumer.stop)

    try:
        await consumer.run()
    finally:
        logger.info("stopping alert-manager")
        await consumer.close()
        await store.close()
        # Close composite notifier (which closes any open httpx clients)
        close = getattr(notifier, "close", None)
        if close is not None and asyncio.iscoroutinefunction(close):
            await close()
        logger.info("alert-manager stopped cleanly")


def main() -> None:
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()