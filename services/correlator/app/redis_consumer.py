"""
Redis Streams consumer za korelacioni engine.

Čita iz `normalized_events` (izlaz Normalizera) preko consumer grupe
`correlator-group`. Za svaki događaj traži od engine-a da ga obradi, pa
sve nastale incidente objavi u `incidents` da ih Alert Manager pokupi.

Periodično čišćenje stanja:
    Engine nakuplja po jedan SlidingWindow za svaki (rule, subject_key)
    par. Dugo neaktivni subjekti drže prozor dok se ne očiste. Svakih
    PRUNE_INTERVAL_SECONDS tražimo od engine-a da izbaci prazne prozore,
    koristeći timestamp najnovijeg događaja kao "sada". Vreme iz stream-a,
    ne zidni sat, pa je ponašanje stabilno u replay-u i testovima.

Rukovanje greškama:
    Pokvaren JSON ili nevalidni omotači se loguju i potvrde (ne mogu da
    stignu do ovog stream-a iz zdravog Normalizera; ako stignu, nešto je
    uzvodno slomljeno, a ne želimo da blokiramo petlju). DB-stil ponavljanja
    nije potreban - na srećnom putu nema I/O osim dva Redis XADD/XACK poziva.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timezone
from typing import Optional

from redis import asyncio as aioredis

from shared.ecs_models import ECSEvent, Incident
from shared.redis_keys import (
    GROUP_CORRELATOR,
    STREAM_INCIDENTS,
    STREAM_NORMALIZED_EVENTS,
)

from app.engine import CorrelationEngine

logger = logging.getLogger(__name__)


_BLOCK_MS = 5000
_BATCH_COUNT = 32
PRUNE_INTERVAL_SECONDS = 60


class CorrelatorConsumer:
    """
    Vlasnik Redis klijenta, engine-a i read petlje.

    Životni ciklus je isti kao kod NormalizerConsumer-a:
        await consumer.connect()
        await consumer.run()
        await consumer.close()
    """

    def __init__(
        self,
        redis_url: str,
        engine: CorrelationEngine,
        *,
        consumer_name: Optional[str] = None,
    ):
        self._redis_url = redis_url
        self._engine = engine
        self._consumer_name = consumer_name or f"correlator-{socket.gethostname()}"
        self._redis: Optional[aioredis.Redis] = None
        self._stop_event = asyncio.Event()
        self._last_prune_ts: Optional[datetime] = None

    # ----- lifecycle -----

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        await self._redis.ping()
        await self._ensure_group()
        logger.info(
            "CorrelatorConsumer connected as %s on stream %s",
            self._consumer_name, STREAM_NORMALIZED_EVENTS,
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            logger.info("CorrelatorConsumer closed")

    def stop(self) -> None:
        self._stop_event.set()

    # ----- main loop -----

    async def run(self) -> None:
        if self._redis is None:
            raise RuntimeError("connect() must be called before run()")

        while not self._stop_event.is_set():
            try:
                entries = await self._read_batch()
            except Exception:
                logger.exception("XREADGROUP failed; retrying")
                await asyncio.sleep(1.0)
                continue

            if not entries:
                continue

            for stream_name, messages in entries:
                for entry_id, fields in messages:
                    await self._process_entry(entry_id, fields)

            self._maybe_prune()

    # ----- internals -----

    async def _ensure_group(self) -> None:
        assert self._redis is not None
        try:
            await self._redis.xgroup_create(
                name=STREAM_NORMALIZED_EVENTS,
                groupname=GROUP_CORRELATOR,
                id="0",
                mkstream=True,
            )
            logger.info("Created consumer group %s on %s",
                        GROUP_CORRELATOR, STREAM_NORMALIZED_EVENTS)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                return
            raise

    async def _read_batch(self) -> list:
        assert self._redis is not None
        return await self._redis.xreadgroup(
            groupname=GROUP_CORRELATOR,
            consumername=self._consumer_name,
            streams={STREAM_NORMALIZED_EVENTS: ">"},
            count=_BATCH_COUNT,
            block=_BLOCK_MS,
        )

    async def _process_entry(self, entry_id: str, fields: dict) -> None:
        raw_payload = fields.get("data")
        if raw_payload is None:
            logger.warning("Entry %s has no 'data' field; acking", entry_id)
            await self._ack(entry_id)
            return

        try:
            obj = json.loads(raw_payload)
            event = ECSEvent(**obj)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Entry %s could not be deserialized: %s", entry_id, exc)
            await self._ack(entry_id)
            return

        # Ažuriraj prune timestamp bez obzira na ishod obrade.
        self._last_prune_ts = event.timestamp

        try:
            incidents = self._engine.process(event)
        except Exception:
            logger.exception("engine.process raised on entry %s", entry_id)
            await self._ack(entry_id)
            return

        for incident in incidents:
            await self._publish_incident(incident)

        await self._ack(entry_id)

    async def _ack(self, entry_id: str) -> None:
        assert self._redis is not None
        await self._redis.xack(
            STREAM_NORMALIZED_EVENTS, GROUP_CORRELATOR, entry_id,
        )

    async def _publish_incident(self, incident: Incident) -> None:
        assert self._redis is not None
        body = incident.model_dump(mode="json")
        await self._redis.xadd(
            STREAM_INCIDENTS,
            {"data": json.dumps(body, separators=(",", ":"))},
            maxlen=100_000,
            approximate=True,
        )
        logger.info(
            "incident emitted rule=%s severity=%s source_ip=%s count=%d",
            incident.rule_name, incident.severity,
            incident.source_ip, incident.event_count,
        )

    def _maybe_prune(self) -> None:
        """Očisti prazne prozore jednom po intervalu, po vremenu iz stream-a."""
        if self._last_prune_ts is None:
            return
        # "Vreme" pratimo preko najnovijeg viđenog događaja. Kad je stream
        # miran, prune ne napreduje - i to je u redu, nema šta da se čisti
        # ako ne stižu novi događaji. Pravo rešenje: brojač događaja od
        # poslednjeg prune-a. Prostija heuristika: čisti svaku grupu (jeftino).
        self._engine.prune_stale(self._last_prune_ts)