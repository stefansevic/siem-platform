"""
Redis Streams consumer for the Alert Manager.

Reads incidents from the Correlator's output stream, applies
deduplication, persists to Postgres, and triggers notifications.

Failure handling:
    - Malformed envelope or invalid Incident JSON: log + ack (we cannot
      do anything useful with it; this is a Correlator bug, not ours).
    - Database error: do NOT ack, so the entry stays pending and is
      redelivered to this consumer on the next read. Notification is
      skipped. (Cross-consumer reclaim via XAUTOCLAIM is not implemented;
      it would be added for horizontal scaling.)
    - Notifier error: already isolated by CompositeNotifier; the entry
      is acked normally because persistence already succeeded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import timedelta
from typing import Optional

from redis import asyncio as aioredis

from shared.ecs_models import Incident
from shared.redis_keys import GROUP_ALERT_MANAGER, STREAM_INCIDENTS

from app.db import IncidentStore
from app.dedup import DedupAction, decide
from app.notifiers import Notifier

logger = logging.getLogger(__name__)


_BLOCK_MS = 5000
_BATCH_COUNT = 32


class AlertManagerConsumer:
    """
    Owns the Redis client, IncidentStore, and Notifier composition.

    Lifecycle mirrors prior consumers:
        await consumer.connect()
        await consumer.run()
        await consumer.close()
    """

    def __init__(
        self,
        redis_url: str,
        store: IncidentStore,
        notifier: Notifier,
        *,
        silence_window: timedelta,
        consumer_name: Optional[str] = None,
    ):
        self._redis_url = redis_url
        self._store = store
        self._notifier = notifier
        self._silence_window = silence_window
        self._consumer_name = (
            consumer_name or f"alert-manager-{socket.gethostname()}"
        )
        self._redis: Optional[aioredis.Redis] = None
        self._stop_event = asyncio.Event()

    # ----- lifecycle -----

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        await self._redis.ping()
        await self._ensure_group()
        logger.info(
            "AlertManagerConsumer connected as %s on stream %s",
            self._consumer_name, STREAM_INCIDENTS,
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            logger.info("AlertManagerConsumer closed")

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

    # ----- internals -----

    async def _ensure_group(self) -> None:
        assert self._redis is not None
        try:
            await self._redis.xgroup_create(
                name=STREAM_INCIDENTS,
                groupname=GROUP_ALERT_MANAGER,
                id="0",
                mkstream=True,
            )
            logger.info(
                "Created consumer group %s on %s",
                GROUP_ALERT_MANAGER, STREAM_INCIDENTS,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                return
            raise

    async def _read_batch(self) -> list:
        assert self._redis is not None
        try:
            return await self._redis.xreadgroup(
                groupname=GROUP_ALERT_MANAGER,
                consumername=self._consumer_name,
                streams={STREAM_INCIDENTS: ">"},
                count=_BATCH_COUNT,
                block=_BLOCK_MS,
            )
        except aioredis.ResponseError as exc:
            # The stream or group can disappear if an operator wipes
            # Redis. Re-create the group on demand and retry next loop.
            if "NOGROUP" in str(exc):
                logger.warning(
                    "consumer group disappeared; recreating and retrying",
                )
                await self._ensure_group()
                return []
            raise

    async def _process_entry(self, entry_id: str, fields: dict) -> None:
        raw_payload = fields.get("data")
        if raw_payload is None:
            logger.warning("Entry %s has no 'data' field; acking", entry_id)
            await self._ack(entry_id)
            return

        # Stage 1: deserialize
        try:
            obj = json.loads(raw_payload)
            new_trigger = Incident(**obj)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "Entry %s could not be deserialized: %s", entry_id, exc,
            )
            await self._ack(entry_id)
            return

        # Stage 2: find existing open match
        try:
            existing = await self._store.find_open_match(
                rule_name=new_trigger.rule_name,
                source_ip=(
                    str(new_trigger.source_ip)
                    if new_trigger.source_ip is not None else None
                ),
            )
        except Exception:
            logger.exception(
                "find_open_match failed for entry %s; leaving unacked",
                entry_id,
            )
            return

        # Stage 3: decide
        decision = decide(
            new_trigger=new_trigger,
            existing_open=existing,
            silence_window=self._silence_window,
        )

        # Stage 4: persist
        try:
            if decision.action == DedupAction.INSERT:
                await self._store.insert(decision.incident)
            else:
                await self._store.update(decision.incident)
        except Exception:
            logger.exception(
                "persistence failed for entry %s; leaving unacked",
                entry_id,
            )
            return

        # Stage 5: notify (failures here do not block ack)
        try:
            await self._notifier.notify(
                decision.incident,
                was_merged=(decision.action == DedupAction.UPDATE),
            )
        except Exception:
            logger.exception(
                "notifier raised for entry %s; persistence already succeeded",
                entry_id,
            )

        # Stage 6: ack
        await self._ack(entry_id)

    async def _ack(self, entry_id: str) -> None:
        assert self._redis is not None
        await self._redis.xack(STREAM_INCIDENTS, GROUP_ALERT_MANAGER, entry_id)