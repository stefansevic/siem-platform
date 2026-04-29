"""
Incident notifiers.

Two notifier implementations are provided:

    ConsoleNotifier — emits a structured JSON log line. Always active.
    WebhookNotifier — POSTs the incident as JSON to a configured URL.
                      Activated only when a webhook URL is set in env.

The CompositeNotifier combines any number of notifiers and dispatches
each incident to all of them concurrently. A failure in one notifier
must not block the others — the loop should keep alerting on every
incident even if the webhook endpoint is down.

Notifiers are agnostic about whether the incident was just inserted
or merged into an existing one. The Alert Manager decides what to
notify on (currently: every dedup decision, including UPDATE — but
see ADR-016 in DECISIONS.md for the silence-window-and-update policy).
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

import httpx

from shared.ecs_models import Incident

logger = logging.getLogger(__name__)


# ============================================
# Base
# ============================================

class Notifier(ABC):
    """Abstract base for incident notifiers."""

    @abstractmethod
    async def notify(self, incident: Incident, *, was_merged: bool) -> None:
        """
        Deliver one incident notification.

        Args:
            incident: the incident, after dedup. For UPDATE actions this
                      is the merged result (with combined event_count
                      and contributing_events).
            was_merged: True if this notification represents an update
                        to an existing incident, False if it is a fresh
                        insert. Receivers may choose to suppress merge
                        notifications if they only want the initial alert.
        """


# ============================================
# Console
# ============================================

class ConsoleNotifier(Notifier):
    """
    Logs the incident as a single-line JSON record. Always active.

    The logger name is fixed so that operators can grep for `alert`
    log lines specifically without sifting through Redis or DB chatter.
    """

    def __init__(self) -> None:
        self._log = logging.getLogger("alert")

    async def notify(self, incident: Incident, *, was_merged: bool) -> None:
        action = "merged" if was_merged else "new"
        # Render once, cheap.
        payload = incident.model_dump(mode="json")
        self._log.warning(
            "incident_%s rule=%s severity=%s source_ip=%s "
            "user=%s count=%d id=%s",
            action,
            incident.rule_name,
            incident.severity,
            incident.source_ip,
            incident.target_user_name,
            incident.event_count,
            incident.id,
        )
        # Detailed JSON in DEBUG so it is available without flooding INFO.
        self._log.debug("incident_payload %s", json.dumps(payload, default=str))


# ============================================
# Webhook
# ============================================

class WebhookNotifier(Notifier):
    """
    POSTs the incident as JSON to a configured URL.

    Failure handling:
        Connection errors, non-2xx responses, and timeouts are logged
        but never raised. The Alert Manager must keep moving even if
        the receiving system is down — incidents are persisted in
        Postgres regardless of webhook delivery.

    Lifecycle:
        The httpx.AsyncClient is created lazily on first notify() call
        and reused for the lifetime of the notifier. close() should be
        called on shutdown to release the connection pool.
    """

    DEFAULT_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        headers: Optional[dict] = None,
    ):
        self._url = url
        self._timeout = timeout_seconds
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._client: Optional[httpx.AsyncClient] = None

    async def notify(self, incident: Incident, *, was_merged: bool) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)

        body = {
            "action": "merged" if was_merged else "new",
            "incident": incident.model_dump(mode="json"),
        }

        try:
            response = await self._client.post(
                self._url, json=body, headers=self._headers,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "webhook delivery failed for incident %s: %s",
                incident.id, exc,
            )
            return

        if response.status_code >= 400:
            logger.warning(
                "webhook returned %d for incident %s: %s",
                response.status_code, incident.id, response.text[:200],
            )
        else:
            logger.info(
                "webhook delivered incident %s -> %s",
                incident.id, response.status_code,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ============================================
# Composite
# ============================================

class CompositeNotifier(Notifier):
    """
    Dispatches each incident to every registered notifier concurrently.

    A failing notifier does not affect the others: each is awaited under
    its own try/except and exceptions are logged. This keeps the Alert
    Manager loop running even when one downstream is misbehaving.
    """

    def __init__(self, notifiers: List[Notifier]):
        if not notifiers:
            raise ValueError("CompositeNotifier needs at least one notifier")
        self._notifiers = list(notifiers)

    async def notify(self, incident: Incident, *, was_merged: bool) -> None:
        # Schedule all notifiers concurrently and wait for all to finish.
        # asyncio.gather with return_exceptions=True ensures one slow
        # or failing notifier does not block or break the others.
        results = await asyncio.gather(
            *[
                self._safe_notify(n, incident, was_merged=was_merged)
                for n in self._notifiers
            ],
            return_exceptions=True,
        )
        for n, result in zip(self._notifiers, results):
            if isinstance(result, Exception):
                logger.exception(
                    "notifier %s raised: %s",
                    type(n).__name__, result,
                )

    @staticmethod
    async def _safe_notify(
        notifier: Notifier, incident: Incident, *, was_merged: bool,
    ) -> None:
        await notifier.notify(incident, was_merged=was_merged)

    async def close(self) -> None:
        for n in self._notifiers:
            close = getattr(n, "close", None)
            if close is not None and asyncio.iscoroutinefunction(close):
                try:
                    await close()
                except Exception:
                    logger.exception("error closing notifier %s", type(n).__name__)