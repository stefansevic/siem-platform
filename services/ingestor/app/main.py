"""
Log Ingestor service.

Responsibilities:
- Receive JSON log events via HTTP POST /logs (push from applications).
- Tail Nginx access.log in real time (pull from filesystem).
- Forward all received logs to the Redis stream `raw_logs`.

The Ingestor is intentionally "dumb": it does NOT parse, normalize, or
filter the payload. Doing nothing complex keeps it fast, reliable, and
easy to scale horizontally.
"""

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from shared.ecs_models import LogFormat, LogSource, RawLogMessage

from .config import settings
from .nginx_tailer import NginxTailer
from .redis_publisher import RedisPublisher


# ============================================
# Structured JSON logging (same approach as demo-webapp)
# ============================================

class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event_data") and isinstance(record.event_data, dict):
            log_obj.update(record.event_data)
        return json.dumps(log_obj, ensure_ascii=False)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_JSONFormatter())
logging.basicConfig(level=settings.log_level, handlers=[_handler], force=True)
logger = logging.getLogger("ingestor")


# ============================================
# Lifespan: setup and teardown
# ============================================

publisher = RedisPublisher()
tailer: NginxTailer | None = None
tailer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize Redis publisher and start Nginx tailer on startup.
    Cleanly stop everything on shutdown.
    """
    global tailer, tailer_task

    logger.info("ingestor_starting")
    await publisher.connect()

    if settings.enable_nginx_tailer:
        tailer = NginxTailer(publisher)
        tailer_task = asyncio.create_task(tailer.run(), name="nginx-tailer")
        logger.info("nginx_tailer_task_scheduled")
    else:
        logger.info("nginx_tailer_disabled_via_env")

    logger.info("ingestor_ready")
    try:
        yield
    finally:
        logger.info("ingestor_shutting_down")
        if tailer is not None:
            tailer.stop()
        if tailer_task is not None:
            try:
                await asyncio.wait_for(tailer_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("nginx_tailer_task_did_not_stop_cleanly")
                tailer_task.cancel()
        await publisher.disconnect()
        logger.info("ingestor_stopped")


# ============================================
# FastAPI application
# ============================================

app = FastAPI(
    title="SIEM Ingestor",
    description="Receives raw logs from web sources and forwards to Redis.",
    version="0.1.0",
    lifespan=lifespan,
)


# ----- Health -----

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ingestor"}


# ----- Push endpoint: accept JSON logs from applications -----

@app.post("/logs", status_code=status.HTTP_202_ACCEPTED)
async def receive_log(request: Request):
    """
    Accepts a JSON body and forwards it to the `raw_logs` stream.

    Request body: any valid JSON object. The Ingestor does not enforce
    a schema -- the Normalizer is responsible for parsing.

    Headers:
        X-Log-Source: optional override of the log source identifier.
                      Defaults to "demo-webapp" when not provided.
    """
    try:
        body_bytes = await request.body()
        if not body_bytes:
            raise HTTPException(status_code=400, detail="Empty body")

        # Re-serialize to ensure we store a clean, single-line JSON string
        try:
            parsed = json.loads(body_bytes)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

        source_header = request.headers.get("x-log-source", "demo-webapp").lower()
        try:
            source_enum = LogSource(source_header)
        except ValueError:
            source_enum = LogSource.UNKNOWN

        message = RawLogMessage(
            source=source_enum,
            format=LogFormat.JSON,
            payload=json.dumps(parsed, ensure_ascii=False),
            origin=f"http://{request.client.host}" if request.client else "http://unknown",
        )

        entry_id = await publisher.publish_raw_log(message)
        if entry_id is None:
            # Publishing failed; surface 503 so caller can retry
            raise HTTPException(status_code=503, detail="Failed to enqueue log")

        return {"accepted": True, "stream_entry_id": entry_id}

    except HTTPException:
        raise
    except Exception:
        logger.exception("receive_log_unexpected_error")
        raise HTTPException(status_code=500, detail="Internal error")


# ----- Stats endpoint (handy for debugging) -----

@app.get("/stats")
async def stats():
    """Reports basic operational state of the ingestor."""
    return {
        "service": "ingestor",
        "redis_host": settings.redis_host,
        "redis_port": settings.redis_port,
        "nginx_tailer_enabled": settings.enable_nginx_tailer,
        "nginx_log_path": settings.nginx_access_log_path,
    }