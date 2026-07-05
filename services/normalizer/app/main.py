"""
Ulazna tačka Normalizer servisa.

Životni ciklus:
    1. Učitaj Settings (odmah pukne ako env nije kompletan).
    2. Podesi JSON logovanje.
    3. Poveži EventWriter na Postgres.
    4. Poveži NormalizerConsumer na Redis (napravi grupu ako treba).
    5. Vrti consumer petlju dosta je degraded .
    6. Zaustavi petlju, zatvori Redis pa Postgres (obrnutim redom).

Servis NE izlaže HTTP; čisto je pozadinski radnik. Zdravlje se vidi
kroz konekciju sa Postgres/Redis i zaostajanje stream-a.
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

    # Primeni index template kad je ES dostupan. Idempotentno:
    # naredni restart ne radi ništa ako template već postoji.
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
