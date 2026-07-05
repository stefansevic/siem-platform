"""
Unit testovi za mapper.

Pokrivaju ceo normalize() tok (parsiranje + mapiranje) sa realnim
RawLogMessage payload-ima. Mapper je mesto gde se dodeljuje bezbednosno
značenje, pa svako korelaciono pravilo ima bar jedan test koji dokazuje
da su njegova potrebna polja prisutna u nastalom ECSEvent-u.
"""

from __future__ import annotations

import pytest

from shared.ecs_models import (
    EventCategory,
    EventOutcome,
    LogFormat,
    LogSource,
    RawLogMessage,
)

from app.mapper import (
    MappingError,
    SkipEvent,
    normalize,
)
from app.parsers import ParseError


# ============================================
# Helpers
# ============================================

def make_raw(payload: str, source: LogSource, fmt: LogFormat) -> RawLogMessage:
    """Pomoćni konstruktor za RawLogMessage u testovima."""
    return RawLogMessage(source=source, format=fmt, payload=payload)


# Realni payload-i (isti oblici kao u test_parsers.py)
NGINX_LOGIN_FAIL = (
    '172.18.0.1 - - [27/Apr/2026:10:05:27 +0000] "POST /login HTTP/1.1" 401 41 '
    '"-" "curl/7.81.0" rt=0.003 uct="0.001" uht="0.001" urt="0.001"'
)

NGINX_GET_OK = (
    '172.18.0.1 - - [27/Apr/2026:10:01:36 +0000] "GET / HTTP/1.1" 200 44 '
    '"-" "curl/7.81.0" rt=0.002 uct="0.001" uht="0.001" urt="0.001"'
)

NGINX_404 = (
    '172.18.0.1 - - [27/Apr/2026:10:01:36 +0000] "GET /admin HTTP/1.1" 404 22 '
    '"-" "dirbuster/1.0" rt=0.001 uct="-" uht="-" urt="-"'
)

WEBAPP_AUTH_FAILURE = (
    '{"timestamp": "2026-04-27T10:05:27.123456+00:00", "level": "WARNING", '
    '"logger": "demo-webapp", "message": "authentication_failure", '
    '"event_type": "authentication", "outcome": "failure", '
    '"username": "admin", "source_ip": "172.18.0.1", '
    '"reason": "invalid_credentials"}'
)

WEBAPP_AUTH_SUCCESS = (
    '{"timestamp": "2026-04-27T10:05:30+00:00", "level": "INFO", '
    '"logger": "demo-webapp", "message": "authentication_success", '
    '"event_type": "authentication", "outcome": "success", '
    '"username": "admin", "source_ip": "172.18.0.1"}'
)

WEBAPP_AUTHZ_FAILURE = (
    '{"timestamp": "2026-04-27T10:10:00+00:00", "level": "WARNING", '
    '"logger": "demo-webapp", "message": "unauthorized_access", '
    '"event_type": "authorization", "outcome": "failure", '
    '"source_ip": "172.18.0.1", "path": "/admin", '
    '"username": "anonymous", "reason": "insufficient_privileges"}'
)

WEBAPP_LIFECYCLE = (
    '{"timestamp": "2026-04-27T10:00:00+00:00", "level": "INFO", '
    '"logger": "demo-webapp", "message": "demo_webapp_started", '
    '"event_type": "lifecycle", "ingestor_url": "http://ingestor:8001/logs"}'
)


# ============================================
# Nginx -> ECSEvent
# ============================================

class TestNginxMapping:
    def test_get_200_is_web_success(self):
        raw = make_raw(NGINX_GET_OK, LogSource.NGINX, LogFormat.NGINX_COMBINED)
        event = normalize(raw)

        assert event.event_category == EventCategory.WEB.value
        assert event.event_outcome == EventOutcome.SUCCESS.value
        assert event.http_response_status_code == 200
        assert event.url_path == "/"
        assert event.log_source == LogSource.NGINX.value

    def test_post_login_401_is_web_failure(self):
        """
        Nginx vidi /login POST 401 kao običan web neuspeh. Bezbednosno
        značajnu verziju daje webapp-ov strukturirani 'authentication'
        događaj, zasebno.
        """
        raw = make_raw(NGINX_LOGIN_FAIL, LogSource.NGINX, LogFormat.NGINX_COMBINED)
        event = normalize(raw)

        assert event.event_category == EventCategory.WEB.value
        assert event.event_outcome == EventOutcome.FAILURE.value
        assert event.http_response_status_code == 401
        assert event.url_path == "/login"

    def test_404_is_failure(self):
        """Osnova za directory scanning pravilo: 404 postaje 'web' + 'failure'."""
        raw = make_raw(NGINX_404, LogSource.NGINX, LogFormat.NGINX_COMBINED)
        event = normalize(raw)

        assert event.event_category == EventCategory.WEB.value
        assert event.event_outcome == EventOutcome.FAILURE.value
        assert event.http_response_status_code == 404
        assert event.user_agent == "dirbuster/1.0"

    def test_raw_message_is_preserved(self):
        raw = make_raw(NGINX_GET_OK, LogSource.NGINX, LogFormat.NGINX_COMBINED)
        event = normalize(raw)
        assert event.raw_message == NGINX_GET_OK


# ============================================
# Webapp -> ECSEvent
# ============================================

class TestWebappMapping:
    def test_auth_failure_has_required_fields(self):
        """Osnova za brute-force pravilo: authentication + failure + source_ip."""
        raw = make_raw(WEBAPP_AUTH_FAILURE, LogSource.DEMO_WEBAPP, LogFormat.JSON)
        event = normalize(raw)

        assert event.event_category == EventCategory.AUTHENTICATION.value
        assert event.event_outcome == EventOutcome.FAILURE.value
        assert str(event.source_ip) == "172.18.0.1"
        assert event.user_name == "admin"

    def test_auth_success_has_required_fields(self):
        """Osnova za account takeover pravilo: authentication + success posle neuspeha."""
        raw = make_raw(WEBAPP_AUTH_SUCCESS, LogSource.DEMO_WEBAPP, LogFormat.JSON)
        event = normalize(raw)

        assert event.event_category == EventCategory.AUTHENTICATION.value
        assert event.event_outcome == EventOutcome.SUCCESS.value
        assert event.user_name == "admin"

    def test_authorization_failure(self):
        raw = make_raw(WEBAPP_AUTHZ_FAILURE, LogSource.DEMO_WEBAPP, LogFormat.JSON)
        event = normalize(raw)

        assert event.event_category == EventCategory.AUTHORIZATION.value
        assert event.event_outcome == EventOutcome.FAILURE.value
        assert event.url_path == "/admin"

    def test_event_action_preserves_original_verb(self):
        """event.action čuva izvorni event_type, za forenzički detalj."""
        raw = make_raw(WEBAPP_AUTH_FAILURE, LogSource.DEMO_WEBAPP, LogFormat.JSON)
        event = normalize(raw)
        assert event.event_action == "authentication"

    def test_lifecycle_is_skipped(self):
        raw = make_raw(WEBAPP_LIFECYCLE, LogSource.DEMO_WEBAPP, LogFormat.JSON)
        with pytest.raises(SkipEvent):
            normalize(raw)


# ============================================
# Outcome derivation rules
# ============================================

class TestOutcomeDerivation:
    @pytest.mark.parametrize("status,expected_outcome", [
        (200, EventOutcome.SUCCESS.value),
        (201, EventOutcome.SUCCESS.value),
        (301, EventOutcome.SUCCESS.value),
        (399, EventOutcome.SUCCESS.value),
        (400, EventOutcome.FAILURE.value),
        (401, EventOutcome.FAILURE.value),
        (403, EventOutcome.FAILURE.value),
        (404, EventOutcome.FAILURE.value),
        (500, EventOutcome.FAILURE.value),
        (599, EventOutcome.FAILURE.value),
    ])
    def test_status_code_to_outcome(self, status, expected_outcome):
        line = (
            f'1.2.3.4 - - [27/Apr/2026:10:00:00 +0000] "GET / HTTP/1.1" {status} 0 '
            f'"-" "curl" rt=0.001 uct="-" uht="-" urt="-"'
        )
        raw = make_raw(line, LogSource.NGINX, LogFormat.NGINX_COMBINED)
        event = normalize(raw)
        assert event.event_outcome == expected_outcome

    def test_explicit_outcome_overrides_status_heuristic(self):
        """
        Webapp auth failure nema status_code, ali ima outcome=failure.
        Mapper mora da koristi eksplicitan ishod.
        """
        raw = make_raw(WEBAPP_AUTH_FAILURE, LogSource.DEMO_WEBAPP, LogFormat.JSON)
        event = normalize(raw)
        assert event.event_outcome == EventOutcome.FAILURE.value


# ============================================
# Error handling
# ============================================

class TestErrors:
    def test_unknown_format_raises_parse_error(self):
        # syslog postoji u enum-u, ali parser za njega ne postoji
        raw = make_raw("anything", LogSource.UNKNOWN, LogFormat.SYSLOG)
        with pytest.raises(ParseError):
            normalize(raw)

    def test_malformed_nginx_raises_parse_error(self):
        raw = make_raw("not a log line", LogSource.NGINX, LogFormat.NGINX_COMBINED)
        with pytest.raises(ParseError):
            normalize(raw)

    def test_malformed_json_raises_parse_error(self):
        raw = make_raw("not json", LogSource.DEMO_WEBAPP, LogFormat.JSON)
        with pytest.raises(ParseError):
            normalize(raw)