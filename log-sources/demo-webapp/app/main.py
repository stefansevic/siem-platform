"""
Demo Web Application - Log Source #2

A minimal FastAPI app that simulates a real web service.
Generates structured JSON logs to stdout for every request,
with special focus on authentication events.

This is intentionally vulnerable for SIEM testing purposes:
- Plaintext passwords (no hashing)
- Predictable test accounts
- Weak rate limiting (none)

DO NOT use this code in production.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel


# ============================================
# Structured JSON logging
# ============================================

class JSONFormatter(logging.Formatter):
    """Formats every log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Attach any extra fields passed via logger.info(..., extra={...})
        if hasattr(record, "event_data") and isinstance(record.event_data, dict):
            log_obj.update(record.event_data)

        # Forward to Ingestor (fire-and-forget, no await)
        if record.name == "demo-webapp":
            _ship_event(log_obj)

        return json.dumps(log_obj, ensure_ascii=False)


# Configure root logger to emit JSON to stdout
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)

logger = logging.getLogger("demo-webapp")

# ============================================
# Forwarder: send each event to the SIEM Ingestor
# ============================================

INGESTOR_URL = os.getenv("INGESTOR_URL", "http://ingestor:8001/logs")
INGESTOR_TIMEOUT = float(os.getenv("INGESTOR_TIMEOUT_SECONDS", "1.0"))

_http_client: Optional[httpx.AsyncClient] = None


async def _forward_to_ingestor(event: dict) -> None:
    """
    Fire-and-forget shipment of a structured event to the Ingestor.

    Failures are logged but never raised: the demo webapp must remain
    responsive even if the SIEM is down.
    """
    global _http_client
    if _http_client is None:
        return  # not yet initialized

    try:
        await _http_client.post(
            INGESTOR_URL,
            json=event,
            headers={"X-Log-Source": "demo-webapp"},
            timeout=INGESTOR_TIMEOUT,
        )
    except Exception as exc:
        # Don't use logger here -- avoid recursive forwarding
        print(
            json.dumps({
                "level": "WARN",
                "logger": "forwarder",
                "message": "ingestor_forward_failed",
                "error": str(exc),
            }),
            file=sys.stdout,
            flush=True,
        )


def _ship_event(event: dict) -> None:
    """Schedule a forward without awaiting it."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_forward_to_ingestor(event))
    except RuntimeError:
        # No running loop yet (e.g., during early init) -- skip
        pass


# ============================================
# Fake user database
# ============================================
# Plaintext on purpose - this is a test target.

USERS = {
    "alice": "Wonderland2024!",
    "bob":   "BuilderBob#42",
    "admin": "admin123",          # weak on purpose - brute force should find it
    "carol": "CarolPass!2024",
}

# Very simple in-memory session store
SESSIONS: dict[str, str] = {}  # session_id -> username


# ============================================
# FastAPI app
# ============================================

app = FastAPI(
    title="SIEM Demo Webapp",
    description="Intentionally vulnerable target for SIEM testing",
    version="0.1.0",
)


def get_client_ip(request: Request) -> str:
    """Extract real client IP, accounting for the Nginx reverse proxy."""
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        # X-Forwarded-For can be a list, take the first
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ----- Middleware: log every request -----

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()

    # Health-check noise: skip logging for the Docker health probe.
    # /health is hit every 5 seconds and is not interesting for SIEM.
    if request.url.path == "/health":
        return await call_next(request)

    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    logger.info(
        "http_request",
        extra={"event_data": {
            "event_type": "http_request",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "source_ip": get_client_ip(request),
            "user_agent": request.headers.get("user-agent", ""),
        }},
    )
    return response


# ============================================
# Endpoints
# ============================================

@app.get("/")
async def root():
    return {"service": "demo-webapp", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


class LoginResponse(BaseModel):
    success: bool
    message: str
    session_id: Optional[str] = None


@app.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """
    Authenticates a user. Always emits an 'authentication' event log
    that the SIEM Normalizer will pick up.
    """
    source_ip = get_client_ip(request)
    expected = USERS.get(username)

    if expected is None or expected != password:
        logger.warning(
            "authentication_failure",
            extra={"event_data": {
                "event_type": "authentication",
                "outcome": "failure",
                "username": username,
                "source_ip": source_ip,
                "reason": "invalid_credentials",
            }},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Successful login
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = username

    logger.info(
        "authentication_success",
        extra={"event_data": {
            "event_type": "authentication",
            "outcome": "success",
            "username": username,
            "source_ip": source_ip,
            "session_id": session_id,
        }},
    )
    return LoginResponse(success=True, message="Login successful", session_id=session_id)


@app.post("/logout")
async def logout(request: Request, session_id: str = Form(...)):
    source_ip = get_client_ip(request)
    username = SESSIONS.pop(session_id, None)

    logger.info(
        "logout",
        extra={"event_data": {
            "event_type": "authentication",
            "outcome": "success" if username else "failure",
            "username": username or "unknown",
            "source_ip": source_ip,
            "action": "logout",
        }},
    )
    return {"success": username is not None}


@app.get("/profile")
async def profile(request: Request, session_id: Optional[str] = None):
    """Protected endpoint - requires valid session."""
    source_ip = get_client_ip(request)

    if not session_id or session_id not in SESSIONS:
        logger.warning(
            "unauthorized_access",
            extra={"event_data": {
                "event_type": "authorization",
                "outcome": "failure",
                "source_ip": source_ip,
                "path": "/profile",
                "reason": "missing_or_invalid_session",
            }},
        )
        raise HTTPException(status_code=403, detail="Not authenticated")

    return {"username": SESSIONS[session_id], "data": "protected profile"}


@app.get("/admin")
async def admin_panel(request: Request, session_id: Optional[str] = None):
    """Admin-only endpoint."""
    source_ip = get_client_ip(request)
    username = SESSIONS.get(session_id) if session_id else None

    if username != "admin":
        logger.warning(
            "unauthorized_access",
            extra={"event_data": {
                "event_type": "authorization",
                "outcome": "failure",
                "source_ip": source_ip,
                "path": "/admin",
                "username": username or "anonymous",
                "reason": "insufficient_privileges",
            }},
        )
        raise HTTPException(status_code=403, detail="Admin access required")

    return {"panel": "admin", "users_count": len(USERS)}

    # ============================================
# Lifecycle: initialize HTTP client at startup
# ============================================

@app.on_event("startup")
async def _on_startup() -> None:
    global _http_client
    _http_client = httpx.AsyncClient()
    logger.info(
        "demo_webapp_started",
        extra={"event_data": {
            "event_type": "lifecycle",
            "ingestor_url": INGESTOR_URL,
        }},
    )


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None