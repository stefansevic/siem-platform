cd ~/projects/siem-platform

cat > services/normalizer/app/main.py << 'PY_EOF'
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


# ============================================
# JSON logging (consistent with demo-webapp/ingestor style)
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


# ============================================
# Service runner
# ============================================

async def run_service() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("normalizer")
    logger.info("starting normalizer")

    writer = EventWriter(settings.postgres_dsn)
    await writer.connect()

    consumer = NormalizerConsumer(
        redis_url=settings.redis_url,
        writer=writer,
    )
    await consumer.connect()

    # Wire SIGINT/SIGTERM to a graceful stop.
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
        # Already handled by signal handler; just exit silently.
        pass


if __name__ == "__main__":
    main()
PY_EOF

