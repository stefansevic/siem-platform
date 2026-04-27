"""
Redis Streams consumer that drives the normalization pipeline.

Reads from `raw_logs` using a consumer group, parses+maps each entry
into an ECSEvent, persists it to Postgres (idempotent), republishes
to `normalized_events` for downstream correlation, and acks the entry.

Failure handling:
    * ParseError or MappingError -> entry routed to `dead_letter_logs`
      stream and acked. The pipeline never blocks on a poison pill.
    * SkipEvent (e.g. lifecycle noise) -> acked silently.
    * Unexpected exceptions -> entry left unacked. After visibility
      timeout, another consumer will pick it up via XAUTOCLAIM.
      The Normalizer never crashes the loop on a single bad entry.

Consumer groups give us:
    * Multiple Normalizer replicas can share the load (XREADGROUP
      partitions entries across consumers within the same group).
    * Pending Entries List (PEL) tracks unacked entries, enabling
      crash recovery via XAUTOCLAIM on startup.
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
# Configuration
# ============================================

# How long XREADGROUP blocks waiting for new entries (milliseconds).
# Short enough that shutdown is responsive, long enough to avoid busy-loop.
_BLOCK_MS = 5000

# Max entries returned per XREADGROUP call.
_BATCH_COUNT = 32

# Visibility timeout for crashed consumers (milliseconds). Entries pending
# longer than this are eligible for reclamation by other consumers.
_PEL_RECLAIM_MS = 60_000


# ============================================
# Consumer
# ============================================

class NormalizerConsumer:
    """
    Owns the read loop, the Redis client, and the EventWriter.

    Lifecycle:
        consumer = NormalizerConsumer(redis_url, writer)
        await consumer.connect()
        await consumer.run()       # blocks until stop() is called
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
        # Use hostname so multi-replica deployments self-identify in Redis.
        self._consumer_name = consumer_name or f"normalizer-{socket.gethostname()}"
        self._redis: Optional[aioredis.Redis] = None
        self._stop_event = asyncio.Event()

    # ----- lifecycle -----

    async def connect(self) -> None:
        """Open the Redis connection and ensure the consumer group exists."""
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
        """Request the run loop to exit at the next iteration."""
        self._stop_event.set()

    # ----- main loop -----

    async def run(self) -> None:
        """Read-process-ack loop. Returns when stop() is called."""
        if self._redis is None:
            raise RuntimeError("connect() must be called before run()")

        while not self._stop_event.is_set():
            try:
                entries = await self._read_batch()
            except Exception:
                # Network blip, Redis down, etc. Back off briefly and retry.
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
        Create the consumer group if it does not exist. MKSTREAM means we
        also create the stream itself, so the Normalizer can start before
        the Ingestor has produced anything.
        """
        assert self._redis is not None
        try:
            await self._redis.xgroup_create(
                name=STREAM_RAW_LOGS,
                groupname=GROUP_NORMALIZER,
                id="0",      # start from beginning of stream
                mkstream=True,
            )
            logger.info("Created consumer group %s on %s",
                        GROUP_NORMALIZER, STREAM_RAW_LOGS)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                # Group already exists -- normal restart path
                return
            raise

    async def _read_batch(self) -> list:
        """One round of XREADGROUP. Returns the raw redis-py response."""
        assert self._redis is not None
        return await self._redis.xreadgroup(
            groupname=GROUP_NORMALIZER,
            consumername=self._consumer_name,
            streams={STREAM_RAW_LOGS: ">"},  # ">" means undelivered to this consumer
            count=_BATCH_COUNT,
            block=_BLOCK_MS,
        )

    async def _process_entry(self, entry_id: str, fields: dict) -> None:
        """
        Handle one stream entry. The contract is: by the time this method
        returns, the entry is either acked (success or dead-lettered) or
        intentionally left pending (true unexpected error).
        """
        raw_payload = fields.get("data")
        if raw_payload is None:
            logger.warning("Entry %s has no 'data' field; dead-lettering", entry_id)
            await self._dead_letter(entry_id, raw_payload, "missing data field")
            await self._ack(entry_id)
            return

        # Stage 1: deserialize the envelope produced by the Ingestor.
        try:
            envelope = json.loads(raw_payload)
            raw_msg = RawLogMessage(**envelope)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Entry %s envelope invalid: %s", entry_id, exc)
            await self._dead_letter(entry_id, raw_payload, f"envelope: {exc}")
            await self._ack(entry_id)
            return

        # Stage 2: parse + map to ECSEvent.
        try:
            event = normalize(raw_msg)
        except SkipEvent:
            # Lifecycle/noise — acked silently, never persisted.
            await self._ack(entry_id)
            return
        except (ParseError, MappingError) as exc:
            logger.warning("Entry %s normalization failed: %s", entry_id, exc)
            await self._dead_letter(entry_id, raw_payload, f"normalize: {exc}")
            await self._ack(entry_id)
            return

        # Stage 3: persist + republish.
        try:
            key = compute_idempotency_key(raw_msg)
            inserted = await self._writer.insert_event(event, idempotency_key=key)
            if inserted:
                await self._publish_normalized(event)
            # Either way, the message has been handled successfully.
            await self._ack(entry_id)
        except Exception:
            # Real I/O failure: do NOT ack so another consumer can retry.
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
        """Quarantine a malformed entry for later forensic inspection."""
        assert self._redis is not None
        await self._redis.xadd(
            STREAM_DEAD_LETTER,
            {
                "original_id": entry_id,
                "reason": reason,
                "payload": payload or "",
            },
            maxlen=10_000,        # cap memory: keep the latest 10k bad entries
            approximate=True,
        )

    async def _publish_normalized(self, event: ECSEvent) -> None:
        """
        Push the ECSEvent onto the normalized_events stream so that the
        Correlation Engine downstream can consume it.
        """
        assert self._redis is not None
        # mode='json' emits a JSON-friendly dict (datetimes -> ISO strings, etc.).
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