"""
Redis Streams consumer koji pokreće normalizacioni pipeline.

Čita iz `raw_logs` preko consumer grupe, svaki unos parsira i mapira u
ECSEvent, upiše ga u Postgres (idempotentno), objavi u
`normalized_events` za korelaciju nizvodno, pa potvrdi unos (XACK).

Rukovanje greškama:
    * ParseError ili MappingError -> unos ide u `dead_letter_logs`

"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Optional

from redis import asyncio as aioredis

from shared.ecs_models import (
    ECSEvent,
    LogFormat,
    LogSource,
    RawLogMessage,
)
from shared.redis_keys import (
    GROUP_NORMALIZER,
    STREAM_DEAD_LETTER,
    STREAM_NORMALIZED_EVENTS,
    STREAM_RAW_LOGS,
)

from app.db import EventWriter
from app.idempotency import compute_idempotency_key
from app.mapper import MappingError, SkipEvent, normalize
from app.parsers import ParseError

logger = logging.getLogger(__name__)


# ============================================
# Konfiguracija
# ============================================

# Koliko XREADGROUP blokira čekajući nove unose (ms). Dovoljno kratko da
# gašenje bude brzo, dovoljno dugo da ne vrtimo praznu petlju.
_BLOCK_MS = 5000

# Najviše unosa po jednom XREADGROUP pozivu.
_BATCH_COUNT = 32

# Napomena: oporavak "pending" unosa palog consumer-a (XAUTOCLAIM) nije
# implementiran. Za jednu instancu to nije potrebno; kod horizontalnog
# skaliranja bi se dodao reclaim. Ranije definisan, a nekorišćen prag je
# uklonjen da kod ne bi tvrdio nešto što ne radi.


# ============================================
# Consumer
# ============================================

class NormalizerConsumer:
    """
    Vlasnik read petlje, Redis klijenta i EventWriter-a.

    Životni ciklus:
        consumer = NormalizerConsumer(redis_url, writer)
        await consumer.connect()
        await consumer.run()      
        await consumer.close()
    """

    def __init__(
        self,
        redis_url: str,
        writer: EventWriter,
        *,
        consumer_name: Optional[str] = None,
    ):
        self._redis_url = redis_url
        self._writer = writer
        self._consumer_name = consumer_name or f"normalizer-{socket.gethostname()}"
        self._redis: Optional[aioredis.Redis] = None
        self._stop_event = asyncio.Event()

    # ----- lifecycle -----

    async def connect(self) -> None:
        """Otvori Redis konekciju i obezbedi da consumer grupa postoji."""
        self._redis = aioredis.from_url(
            self._redis_url, decode_responses=True
        )
        await self._redis.ping()
        await self._ensure_group()
        logger.info(
            "NormalizerConsumer connected as %s on stream %s",
            self._consumer_name, STREAM_RAW_LOGS,
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            logger.info("NormalizerConsumer closed")

    def stop(self) -> None:
        """Zatraži da run petlja izađe u sledećoj iteraciji."""
        self._stop_event.set()

    # ----- glavna petlja -----

    async def run(self) -> None:
        """Petlja: pročitaj - obradi - potvrdi. Izlazi kad se pozove stop()."""
        if self._redis is None:
            raise RuntimeError("connect() must be called before run()")

        while not self._stop_event.is_set():
            try:
                entries = await self._read_batch()
            except Exception:
                # Mrežni prekid, Redis pao, itd. Kratko sačekaj pa probaj opet.
                logger.exception("XREADGROUP failed; retrying")
                await asyncio.sleep(1.0)
                continue

            if not entries:
                continue

            for stream_name, messages in entries:
                for entry_id, fields in messages:
                    await self._process_entry(entry_id, fields)

    # ----- internals -----

    async def _ensure_group(self) -> None:
        """
        Napravi consumer grupu ako ne postoji. Ako postoji, samo se poveži.
        
        """
        assert self._redis is not None
        try:
            await self._redis.xgroup_create(
                name=STREAM_RAW_LOGS,
                groupname=GROUP_NORMALIZER,
                id="0",      # kreni od početka stream-a
                mkstream=True,
            )
            logger.info("Created consumer group %s on %s",
                        GROUP_NORMALIZER, STREAM_RAW_LOGS)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                # Grupa već postoji - normalan put pri restartu
                return
            raise

    async def _read_batch(self) -> list:
        """Jedan krug XREADGROUP-a. Vraća sirov redis-py odgovor."""
        assert self._redis is not None
        return await self._redis.xreadgroup(
            groupname=GROUP_NORMALIZER,
            consumername=self._consumer_name,
            streams={STREAM_RAW_LOGS: ">"},  # ">" znači neisporučeno ovom consumer-u
            count=_BATCH_COUNT,
            block=_BLOCK_MS,
        )

    async def _process_entry(self, entry_id: str, fields: dict) -> None:
        """
        Obradi jedan unos iz stream-a. Ugovor: kad ova metoda vrati,
        unos je ili potvrđen (uspeh ili dead-letter) ili namerno ostavljen
        pending (stvarno neočekivana greška).
        """
        raw_payload = fields.get("data")
        if raw_payload is None:
            logger.warning("Entry %s has no 'data' field; dead-lettering", entry_id)
            await self._dead_letter(entry_id, raw_payload, "missing data field")
            await self._ack(entry_id)
            return

        # Korak 1: deserijalizuj omotač koji je napravio Ingestor.
        try:
            envelope = json.loads(raw_payload)
            raw_msg = RawLogMessage(**envelope)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Entry %s envelope invalid: %s", entry_id, exc)
            await self._dead_letter(entry_id, raw_payload, f"envelope: {exc}")
            await self._ack(entry_id)
            return

        # Korak 2: parsiraj i mapiraj u ECSEvent.
        try:
            event = normalize(raw_msg)
        except SkipEvent:
            # Lifecycle/šum - tiho potvrdi, nikad ne čuvaj.
            await self._ack(entry_id)
            return
        except (ParseError, MappingError) as exc:
            logger.warning("Entry %s normalization failed: %s", entry_id, exc)
            await self._dead_letter(entry_id, raw_payload, f"normalize: {exc}")
            await self._ack(entry_id)
            return

        # Korak 3: sačuvaj i objavi dalje.
        try:
            key = compute_idempotency_key(raw_msg)
            # Upis je idempotentan (ON CONFLICT DO NOTHING). Objavu NE vezujemo
            # za rezultat upisa: ako je prethodni pokušaj upisao red a pao pre
            # objave, na ponovnoj dostavi upis vraća "duplikat", ali događaj
            # svejedno mora da stigne do korelatora. Zato UVEK objavljujemo;
            # korelator dedupira po event.id, pa dupla objava ne udvaja brojače.
            await self._writer.insert_event(event, idempotency_key=key)
            await self._publish_normalized(event)
            await self._ack(entry_id)
        except Exception:
            # Prava I/O greška: NE potvrđuj, da drugi consumer proba ponovo.
            logger.exception("Entry %s persistence/publish failed; leaving unacked",
                             entry_id)
            return

    # ----- helpers -----

    async def _ack(self, entry_id: str) -> None:
        assert self._redis is not None
        await self._redis.xack(STREAM_RAW_LOGS, GROUP_NORMALIZER, entry_id)

    async def _dead_letter(
        self, entry_id: str, payload: Optional[str], reason: str,
    ) -> None:
        """Skloni pokvaren unos u karantin, za kasniju forenzičku analizu."""
        assert self._redis is not None
        await self._redis.xadd(
            STREAM_DEAD_LETTER,
            {
                "original_id": entry_id,
                "reason": reason,
                "payload": payload or "",
            },
            maxlen=10_000,        # ograniči memoriju: čuvaj poslednjih 10k loših unosa
            approximate=True,
        )

    async def _publish_normalized(self, event: ECSEvent) -> None:
        """
        Gurni ECSEvent u normalized_events stream, da ga Correlator
        nizvodno pokupi.
        """
        assert self._redis is not None
        # mode='json' daje dict pogodan za JSON (datetime -> ISO string, itd.).
        body = event.model_dump(mode="json")
        await self._redis.xadd(
            STREAM_NORMALIZED_EVENTS,
            {"data": json.dumps(body, separators=(",", ":"))},
            maxlen=100_000,
            approximate=True,
        )


# ============================================
# DSN helper
# ============================================

def build_redis_url(
    host: Optional[str] = None,
    port: Optional[str] = None,
    db: Optional[str] = None,
) -> str:
    host = host or os.environ.get("REDIS_HOST", "redis")
    port = port or os.environ.get("REDIS_PORT", "6379")
    db = db or os.environ.get("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"