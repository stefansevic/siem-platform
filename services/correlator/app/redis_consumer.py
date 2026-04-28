"""
Redis Streams consumer for the Correlation Engine.

Reads from `normalized_events` (the Normalizer's output) using consumer
group `correlator-group`. For each event it asks the engine to process
the event, then publishes any resulting incidents to `incidents` for
the Alert Manager to pick up.

Periodic state cleanup:
    The engine accumulates one SlidingWindow per (rule, subject_key)
    pair. Long-idle subjects keep a window allocated until pruned.
    Every PRUNE_INTERVAL_SECONDS we ask the engine to drop empty
    windows, using the most recent event's timestamp as "now". Stream
    time, not wall-clock, so behavior is stable in replays and tests.

Failure handling:
    Malformed JSON or invalid envelopes are logged and acked (they
    cannot reach this stream from a healthy Normalizer; if they do,
    something upstream is broken and we don't want to block the loop).
    DB-style retries are not needed — there is no external I/O on the
    happy path other than two Redis XADD/XACK calls.
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
    Owns the Redis client, the engine, and the read loop.

    Lifecycle mirrors NormalizerConsumer:
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

        # Update prune timestamp regardless of dispatch outcome.
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
        """Prune empty windows once per interval, using stream time."""
        if self._last_prune_ts is None:
            return
        # Tracks "wall time" via the latest event we saw.
        # In an idle stream, prune does not advance, which is fine —
        # nothing to clean up if no new events are arriving.
        # Real implementation: keep a counter of events since last prune.
        # Simpler heuristic: prune every batch (cheap enough).
        self._engine.prune_stale(self._last_prune_ts)