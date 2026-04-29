"""
Unit tests for incident notifiers.

ConsoleNotifier — verified by capturing log records.
WebhookNotifier — verified via pytest-httpx, which intercepts httpx
                  requests without spinning up a real HTTP server.
CompositeNotifier — verified to dispatch to all members and survive
                    a failing one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List
from uuid import uuid4

import httpx
import pytest

from shared.ecs_models import Incident, IncidentSeverity, IncidentStatus

from app.notifiers import (
    CompositeNotifier,
    ConsoleNotifier,
    Notifier,
    WebhookNotifier,
)

pytestmark = pytest.mark.asyncio


# ============================================
# Helpers
# ============================================

def make_incident(rule_name: str = "brute_force") -> Incident:
    return Incident(
        id=uuid4(),
        rule_name=rule_name,
        rule_version="1.0",
        severity=IncidentSeverity.HIGH,
        first_event_at=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),
        last_event_at=datetime(2026, 4, 28, 12, 1, tzinfo=timezone.utc),
        source_ip="1.2.3.4",
        target_user_name="admin",
        event_count=5,
        contributing_events=[uuid4() for _ in range(5)],
        status=IncidentStatus.OPEN,
    )


# ============================================
# ConsoleNotifier
# ============================================

class TestConsoleNotifier:
    async def test_logs_at_warning_level(self, caplog):
        notifier = ConsoleNotifier()
        incident = make_incident()

        with caplog.at_level(logging.WARNING, logger="alert"):
            await notifier.notify(incident, was_merged=False)

        assert any(
            "incident_new" in r.message and "brute_force" in r.message
            for r in caplog.records
        )

    async def test_merge_action_in_log(self, caplog):
        notifier = ConsoleNotifier()
        incident = make_incident()

        with caplog.at_level(logging.WARNING, logger="alert"):
            await notifier.notify(incident, was_merged=True)

        assert any("incident_merged" in r.message for r in caplog.records)

    async def test_includes_severity_and_count(self, caplog):
        notifier = ConsoleNotifier()
        incident = make_incident()

        with caplog.at_level(logging.WARNING, logger="alert"):
            await notifier.notify(incident, was_merged=False)

        message = caplog.records[-1].message
        assert "severity=high" in message
        assert "count=5" in message
        assert "1.2.3.4" in message


# ============================================
# WebhookNotifier
# ============================================

class TestWebhookNotifier:
    async def test_posts_json_to_configured_url(self, httpx_mock):
        url = "https://example.com/hooks/siem"
        httpx_mock.add_response(method="POST", url=url, status_code=200)

        notifier = WebhookNotifier(url)
        await notifier.notify(make_incident(), was_merged=False)
        await notifier.close()

        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"

    async def test_payload_includes_action_and_incident(self, httpx_mock):
        url = "https://example.com/hook"
        httpx_mock.add_response(method="POST", url=url, status_code=200)

        notifier = WebhookNotifier(url)
        await notifier.notify(make_incident(), was_merged=True)
        await notifier.close()

        import json
        body = json.loads(httpx_mock.get_request().content)
        assert body["action"] == "merged"
        assert body["incident"]["rule_name"] == "brute_force"
        assert body["incident"]["severity"] == "high"

    async def test_4xx_response_does_not_raise(self, httpx_mock, caplog):
        url = "https://example.com/hook"
        httpx_mock.add_response(method="POST", url=url, status_code=500)

        notifier = WebhookNotifier(url)
        # Must not raise
        with caplog.at_level(logging.WARNING):
            await notifier.notify(make_incident(), was_merged=False)
        await notifier.close()

        assert any("webhook returned 500" in r.message for r in caplog.records)

    async def test_connection_error_does_not_raise(self, httpx_mock, caplog):
        url = "https://example.com/hook"
        httpx_mock.add_exception(httpx.ConnectError("boom"))

        notifier = WebhookNotifier(url)
        with caplog.at_level(logging.WARNING):
            await notifier.notify(make_incident(), was_merged=False)
        await notifier.close()

        assert any("webhook delivery failed" in r.message for r in caplog.records)


# ============================================
# CompositeNotifier
# ============================================

class _RecordingNotifier(Notifier):
    """Test double that records every call."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    async def notify(self, incident: Incident, *, was_merged: bool) -> None:
        self.calls.append((incident.id, was_merged))


class _RaisingNotifier(Notifier):
    """Test double that always raises."""

    async def notify(self, incident: Incident, *, was_merged: bool) -> None:
        raise RuntimeError("boom")


class TestCompositeNotifier:
    async def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            CompositeNotifier([])

    async def test_dispatches_to_all_members(self):
        a = _RecordingNotifier()
        b = _RecordingNotifier()
        composite = CompositeNotifier([a, b])

        incident = make_incident()
        await composite.notify(incident, was_merged=False)

        assert a.calls == [(incident.id, False)]
        assert b.calls == [(incident.id, False)]

    async def test_failing_notifier_does_not_block_others(self, caplog):
        good = _RecordingNotifier()
        bad = _RaisingNotifier()
        composite = CompositeNotifier([bad, good, bad])

        incident = make_incident()
        with caplog.at_level(logging.ERROR):
            await composite.notify(incident, was_merged=False)

        # Good notifier was still called
        assert good.calls == [(incident.id, False)]
        # Errors from bad notifiers were logged
        assert sum("notifier _RaisingNotifier raised" in r.message
                   for r in caplog.records) == 2