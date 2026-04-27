"""
Pure parser functions for raw log payloads.

Two formats are supported:
    - Nginx siem_combined  (text, regex)
    - Demo webapp JSON     (json.loads + dict access)

Each parser returns a `ParsedFields` dataclass — a flat, source-neutral
intermediate representation. The mapper module converts ParsedFields into
the canonical ECSEvent.

These functions are intentionally I/O-free so they can be unit tested
without Redis, Postgres, or any container running.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ============================================
# Intermediate representation
# ============================================

@dataclass
class ParsedFields:
    """
    Source-neutral parsed fields. Not all fields are present for all
    log types; the mapper decides what becomes an ECSEvent.
    """
    timestamp: Optional[datetime] = None
    source_ip: Optional[str] = None
    http_method: Optional[str] = None
    url_path: Optional[str] = None
    http_status: Optional[int] = None
    user_agent: Optional[str] = None
    user_name: Optional[str] = None
    event_type: Optional[str] = None    # "authentication" | "http_request" | "authorization" | ...
    outcome: Optional[str] = None       # "success" | "failure"
    extras: dict[str, Any] = field(default_factory=dict)


class ParseError(ValueError):
    """Raised when a payload cannot be parsed by the chosen parser."""


# ============================================
# Nginx siem_combined parser
# ============================================
# Format definition (from log-sources/nginx/nginx.conf):
#   $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent
#   "$http_referer" "$http_user_agent"
#   rt=$request_time uct="$upstream_connect_time"
#   uht="$upstream_header_time" urt="$upstream_response_time"
#
# Real example:
#   172.18.0.1 - - [27/Apr/2026:10:05:27 +0000] "POST /login HTTP/1.1" 401 41
#   "-" "curl/7.81.0" rt=0.002 uct="0.001" uht="0.001" urt="0.001"

_NGINX_SIEM_COMBINED_RE = re.compile(
    r'^(?P<remote_addr>\S+)\s+'
    r'-\s+'
    r'(?P<remote_user>\S+)\s+'
    r'\[(?P<time_local>[^\]]+)\]\s+'
    r'"(?P<method>[A-Z]+)\s+(?P<path>[^"\s]+)\s+HTTP/[\d.]+"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<body_bytes>\d+|-)\s+'
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<user_agent>[^"]*)"\s+'
    r'rt=(?P<rt>[\d.]+|-)\s+'
    r'uct="(?P<uct>[\d.]*|-)"\s+'
    r'uht="(?P<uht>[\d.]*|-)"\s+'
    r'urt="(?P<urt>[\d.]*|-)"'
    r'\s*$'
)

# Nginx $time_local format: 27/Apr/2026:10:05:27 +0000
_NGINX_TIME_FORMAT = "%d/%b/%Y:%H:%M:%S %z"


def parse_nginx_siem_combined(payload: str) -> ParsedFields:
    """
    Parse one line of Nginx access log in `siem_combined` format.

    Raises ParseError if the line does not match the expected shape.
    """
    line = payload.strip()
    if not line:
        raise ParseError("empty payload")

    match = _NGINX_SIEM_COMBINED_RE.match(line)
    if match is None:
        raise ParseError(f"line does not match siem_combined format: {line!r}")

    g = match.groupdict()

    try:
        ts = datetime.strptime(g["time_local"], _NGINX_TIME_FORMAT)
    except ValueError as exc:
        raise ParseError(f"invalid time_local: {g['time_local']!r}") from exc

    # Normalize to UTC (Nginx already emits offset, just convert)
    ts_utc = ts.astimezone(timezone.utc)

    user_agent = g["user_agent"] if g["user_agent"] != "-" else None

    return ParsedFields(
        timestamp=ts_utc,
        source_ip=g["remote_addr"],
        http_method=g["method"],
        url_path=g["path"],
        http_status=int(g["status"]),
        user_agent=user_agent,
        user_name=None,  # Nginx access log has no auth context
        event_type="http_request",
        outcome=None,    # outcome is decided by the mapper from status code
        extras={
            "rt": _safe_float(g["rt"]),
            "uct": _safe_float(g["uct"]),
            "uht": _safe_float(g["uht"]),
            "urt": _safe_float(g["urt"]),
        },
    )


def _safe_float(value: str) -> Optional[float]:
    """Return float or None if value is '-' or empty."""
    if value in ("-", "", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ============================================
# Demo webapp JSON parser
# ============================================
# Two event types matter for correlation:
#   1. event_type=http_request    — every request (middleware)
#   2. event_type=authentication  — login success/failure
# Plus event_type=authorization (unauthorized_access) and lifecycle (ignored).
#
# Real example (auth failure):
#   {"timestamp":"2026-04-27T10:05:27.123456+00:00","level":"WARNING",
#    "logger":"demo-webapp","message":"authentication_failure",
#    "event_type":"authentication","outcome":"failure","username":"admin",
#    "source_ip":"172.18.0.1","reason":"invalid_credentials"}

_KNOWN_EVENT_TYPES = {
    "http_request",
    "authentication",
    "authorization",
    "lifecycle",
}


def parse_demo_webapp_json(payload: str) -> ParsedFields:
    """
    Parse one JSON log line emitted by the demo webapp.

    Raises ParseError on invalid JSON or missing required fields.
    """
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise ParseError(f"expected JSON object, got {type(obj).__name__}")

    # timestamp is required for any meaningful event
    ts_raw = obj.get("timestamp")
    if not ts_raw:
        raise ParseError("missing 'timestamp' field")

    try:
        ts = datetime.fromisoformat(ts_raw)
    except (TypeError, ValueError) as exc:
        raise ParseError(f"invalid timestamp: {ts_raw!r}") from exc

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_utc = ts.astimezone(timezone.utc)

    event_type = obj.get("event_type")
    # Don't reject unknown types — mapper may still want to persist them
    # as 'web' category. We just record what we saw.

    status_code = obj.get("status_code")
    if status_code is not None:
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            status_code = None

    return ParsedFields(
        timestamp=ts_utc,
        source_ip=obj.get("source_ip"),
        http_method=obj.get("method"),
        url_path=obj.get("path"),
        http_status=status_code,
        user_agent=obj.get("user_agent"),
        user_name=obj.get("username"),
        event_type=event_type,
        outcome=obj.get("outcome"),
        extras={
            k: v for k, v in obj.items()
            if k not in {
                "timestamp", "source_ip", "method", "path", "status_code",
                "user_agent", "username", "event_type", "outcome",
                "level", "logger", "message",
            }
        },
    )