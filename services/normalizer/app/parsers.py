"""
Čiste parser funkcije za sirove logove.

Podržana dva formata:
    - Nginx siem_combined  
    - Demo webapp JSON     

Svaki parser vraća `ParsedFields` - neutralnu među-strukturu.
Mapper je kasnije pretvara u kanonski ECSEvent.

"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional



@dataclass
class ParsedFields:
    """
    Parsirana polja neutralna na izvor. Nemaju svi logovi sva polja.
    """
    timestamp: Optional[datetime] = None
    source_ip: Optional[str] = None
    http_method: Optional[str] = None
    url_path: Optional[str] = None
    http_status: Optional[int] = None
    user_agent: Optional[str] = None
    user_name: Optional[str] = None
    event_type: Optional[str] = None    
    outcome: Optional[str] = None       
    extras: dict[str, Any] = field(default_factory=dict)


class ParseError(ValueError):
    """Diže se kad izabrani parser ne može da obradi payload."""

# ============================================
# Parser za nginx logove
# ============================================

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

_NGINX_TIME_FORMAT = "%d/%b/%Y:%H:%M:%S %z"


def parse_nginx_siem_combined(payload: str) -> ParsedFields:
    """
    Parsira jednu liniju Nginx access log-a u `siem_combined` formatu.

    Diže ParseError ako linija ne odgovara očekivanom obliku.
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

    ts_utc = ts.astimezone(timezone.utc)

    user_agent = g["user_agent"] if g["user_agent"] != "-" else None

    return ParsedFields(
        timestamp=ts_utc,
        source_ip=g["remote_addr"],
        http_method=g["method"],
        url_path=g["path"],
        http_status=int(g["status"]),
        user_agent=user_agent,
        user_name=None,  # Nginx access log nema podatke o autentifikaciji
        event_type="http_request",
        outcome=None,    # ishod mapper određuje iz status koda
        extras={
            "rt": _safe_float(g["rt"]),
            "uct": _safe_float(g["uct"]),
            "uht": _safe_float(g["uht"]),
            "urt": _safe_float(g["urt"]),
        },
    )


def _safe_float(value: str) -> Optional[float]:
    """Vrati float, ili None ako je vrednost '-' ili prazna."""
    if value in ("-", "", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ============================================
# Parser za Demo webapp JSON
# ============================================


_KNOWN_EVENT_TYPES = {
    "http_request",
    "authentication",
    "authorization",
    "lifecycle",
}


def parse_demo_webapp_json(payload: str) -> ParsedFields:
    """
    Parsira jednu JSON log liniju koju emituje demo webapp.

    Diže ParseError na nevalidan JSON.
    """
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise ParseError(f"expected JSON object, got {type(obj).__name__}")

    # timestamp je obavezan za svaki smislen događaj
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