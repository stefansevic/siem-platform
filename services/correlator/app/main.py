"""
Ulazna tačka Correlator servisa.

Životni ciklus:
    1. Učitaj Settings.
    2. Podesi JSON logovanje.
    3. Napravi CorrelationEngine sa sva tri pravila, parametri dolaze iz
       Settings-a (pa se pragovi menjaju bez promene koda).
    4. Poveži CorrelatorConsumer na Redis.
    5. Vrti read petlju do SIGINT/SIGTERM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.engine import CorrelationEngine
from app.redis_consumer import CorrelatorConsumer
from app.rules import (
    AccountTakeoverRule,
    BruteForceRule,
    DirectoryScanningRule,
)


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


def _build_engine(settings: Settings) -> CorrelationEngine:
    return CorrelationEngine([
        BruteForceRule(
            threshold=settings.brute_force_threshold,
            window=timedelta(seconds=settings.brute_force_window_seconds),
        ),
        DirectoryScanningRule(
            threshold=settings.dir_scan_threshold,
            window=timedelta(seconds=settings.dir_scan_window_seconds),
        ),
        AccountTakeoverRule(
            failure_threshold=settings.ato_failure_threshold,
            window=timedelta(seconds=settings.ato_window_seconds),
        ),
    ])


async def run_service() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("correlator")
    logger.info("starting correlator")

    engine = _build_engine(settings)
    logger.info(
        "engine ready with %d rules: %s",
        len(engine.rules),
        ", ".join(r.name for r in engine.rules),
    )

    consumer = CorrelatorConsumer(
        redis_url=settings.redis_url,
        engine=engine,
    )
    await consumer.connect()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, consumer.stop)

    try:
        await consumer.run()
    finally:
        logger.info("stopping correlator")
        await consumer.close()
        logger.info("correlator stopped cleanly")


def main() -> None:
    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()