"""
Unit testovi za parser funkcije.

Primeri linija ispod su stvarno uhvaćeni iz Redis stream-a tokom
end-to-end testiranja.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.parsers import (
    ParsedFields,
    ParseError,
    parse_demo_webapp_json,
    parse_nginx_siem_combined,
)


# ============================================
# Nginx siem_combined
# ============================================

NGINX_GET_OK = (
    '172.18.0.1 - - [27/Apr/2026:10:01:36 +0000] "GET / HTTP/1.1" 200 44 '
    '"-" "curl/7.81.0" rt=0.002 uct="0.001" uht="0.001" urt="0.001"'
)

NGINX_POST_LOGIN_FAIL = (
    '172.18.0.1 - - [27/Apr/2026:10:05:27 +0000] "POST /login HTTP/1.1" 401 41 '
    '"-" "curl/7.81.0" rt=0.003 uct="0.001" uht="0.001" urt="0.001"'
)

NGINX_GET_404 = (
    '172.18.0.1 - - [27/Apr/2026:10:01:36 +0000] "GET /nonexistent HTTP/1.1" 404 22 '
    '"-" "curl/7.81.0" rt=0.001 uct="-" uht="-" urt="-"'
)


class TestNginxParser:
    def test_parses_basic_get_200(self):
        result = parse_nginx_siem_combined(NGINX_GET_OK)

        assert result.source_ip == "172.18.0.1"
        assert result.http_method == "GET"
        assert result.url_path == "/"
        assert result.http_status == 200
        assert result.user_agent == "curl/7.81.0"
        assert result.event_type == "http_request"
        assert result.outcome is None  # Nginx parser ne zaključuje ishod
        assert result.user_name is None

    def test_parses_post_login_failure(self):
        result = parse_nginx_siem_combined(NGINX_POST_LOGIN_FAIL)

        assert result.http_method == "POST"
        assert result.url_path == "/login"
        assert result.http_status == 401

    def test_parses_404(self):
        result = parse_nginx_siem_combined(NGINX_GET_404)

        assert result.http_status == 404
        assert result.url_path == "/nonexistent"

    def test_timestamp_is_utc(self):
        result = parse_nginx_siem_combined(NGINX_GET_OK)

        assert result.timestamp is not None
        assert result.timestamp.tzinfo == timezone.utc
        assert result.timestamp.year == 2026
        assert result.timestamp.month == 4
        assert result.timestamp.day == 27

    def test_dash_user_agent_becomes_none(self):
        line = (
            '10.0.0.1 - - [27/Apr/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 0 '
            '"-" "-" rt=0.000 uct="-" uht="-" urt="-"'
        )
        result = parse_nginx_siem_combined(line)
        assert result.user_agent is None

    def test_extras_capture_timing_fields(self):
        result = parse_nginx_siem_combined(NGINX_GET_OK)

        assert result.extras["rt"] == 0.002
        assert result.extras["uct"] == 0.001

    def test_dash_in_timing_becomes_none(self):
        result = parse_nginx_siem_combined(NGINX_GET_404)

        assert result.extras["uct"] is None
        assert result.extras["urt"] is None

    def test_empty_payload_raises(self):
        with pytest.raises(ParseError):
            parse_nginx_siem_combined("")

    def test_malformed_payload_raises(self):
        with pytest.raises(ParseError):
            parse_nginx_siem_combined("this is not an nginx log line")

    def test_garbage_status_does_not_match(self):
        # Status mora biti tačno 3 cifre, inače ceo regex ne prolazi.
        line = (
            '1.1.1.1 - - [27/Apr/2026:10:00:00 +0000] "GET / HTTP/1.1" 99 0 '
            '"-" "curl" rt=0.001 uct="-" uht="-" urt="-"'
        )
        with pytest.raises(ParseError):
            parse_nginx_siem_combined(line)


# ============================================
# Demo webapp JSON
# ============================================

WEBAPP_AUTH_FAILURE = (
    '{"timestamp": "2026-04-27T10:05:27.123456+00:00", "level": "WARNING", '
    '"logger": "demo-webapp", "message": "authentication_failure", '
    '"event_type": "authentication", "outcome": "failure", '
    '"username": "admin", "source_ip": "172.18.0.1", '
    '"reason": "invalid_credentials"}'
)

WEBAPP_AUTH_SUCCESS = (
    '{"timestamp": "2026-04-27T10:05:27.789012+00:00", "level": "INFO", '
    '"logger": "demo-webapp", "message": "authentication_success", '
    '"event_type": "authentication", "outcome": "success", '
    '"username": "admin", "source_ip": "172.18.0.1", '
    '"session_id": "b3a1196f-9c58-4910-a177-33c50097e8a1"}'
)

WEBAPP_HTTP_REQUEST = (
    '{"timestamp": "2026-04-27T10:04:17.779067+00:00", "level": "INFO", '
    '"logger": "demo-webapp", "message": "http_request", '
    '"event_type": "http_request", "method": "POST", "path": "/login", '
    '"status_code": 422, "duration_ms": 0.43, '
    '"source_ip": "172.18.0.1", "user_agent": "curl/7.81.0"}'
)

WEBAPP_UNAUTHORIZED = (
    '{"timestamp": "2026-04-27T10:10:00+00:00", "level": "WARNING", '
    '"logger": "demo-webapp", "message": "unauthorized_access", '
    '"event_type": "authorization", "outcome": "failure", '
    '"source_ip": "172.18.0.1", "path": "/admin", '
    '"username": "anonymous", "reason": "insufficient_privileges"}'
)


class TestWebappJsonParser:
    def test_parses_authentication_failure(self):
        result = parse_demo_webapp_json(WEBAPP_AUTH_FAILURE)

        assert result.event_type == "authentication"
        assert result.outcome == "failure"
        assert result.user_name == "admin"
        assert result.source_ip == "172.18.0.1"
        assert result.extras.get("reason") == "invalid_credentials"

    def test_parses_authentication_success(self):
        result = parse_demo_webapp_json(WEBAPP_AUTH_SUCCESS)

        assert result.event_type == "authentication"
        assert result.outcome == "success"
        assert result.user_name == "admin"
        # session_id treba da bude u extras
        assert "session_id" in result.extras

    def test_parses_http_request(self):
        result = parse_demo_webapp_json(WEBAPP_HTTP_REQUEST)

        assert result.event_type == "http_request"
        assert result.http_method == "POST"
        assert result.url_path == "/login"
        assert result.http_status == 422
        assert result.user_agent == "curl/7.81.0"

    def test_parses_authorization_failure(self):
        result = parse_demo_webapp_json(WEBAPP_UNAUTHORIZED)

        assert result.event_type == "authorization"
        assert result.outcome == "failure"
        assert result.url_path == "/admin"
        assert result.user_name == "anonymous"

    def test_timestamp_is_utc(self):
        result = parse_demo_webapp_json(WEBAPP_AUTH_FAILURE)

        assert result.timestamp is not None
        assert result.timestamp.tzinfo is not None
        assert result.timestamp == datetime(
            2026, 4, 27, 10, 5, 27, 123456, tzinfo=timezone.utc
        )

    def test_invalid_json_raises(self):
        with pytest.raises(ParseError):
            parse_demo_webapp_json("not json {{{")

    def test_non_object_json_raises(self):
        with pytest.raises(ParseError):
            parse_demo_webapp_json('["array", "is", "not", "object"]')

    def test_missing_timestamp_raises(self):
        with pytest.raises(ParseError):
            parse_demo_webapp_json('{"event_type": "test"}')

    def test_invalid_timestamp_raises(self):
        with pytest.raises(ParseError):
            parse_demo_webapp_json('{"timestamp": "not a date"}')

    def test_unknown_event_type_does_not_raise(self):
        """Parser je popustljiv - nepoznati event_type-ovi prolaze."""
        payload = (
            '{"timestamp": "2026-04-27T10:00:00+00:00", '
            '"event_type": "future_unknown_type"}'
        )
        result = parse_demo_webapp_json(payload)
        assert result.event_type == "future_unknown_type"