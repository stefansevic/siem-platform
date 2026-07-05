"""
Ingestor - ulazna tačka sistema za logove.

Šta radi:
- Prima JSON logove preko HTTP POST /logs (PUSH).
- Tail-uje Nginx access.log u realnom vremenu (PULL).
- Sve primljene logove prosleđuje u Redis stream `raw_logs`.

Namerno je "glup": ne parsira, ne normalizuje i ne filtrira sadržaj.
Time ostaje brz i pouzdan. 
Sva "pamet" (parsiranje, normalizacija) je posao Normalizera .
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
# JSON logovanje (isti pristup kao demo-webapp)
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
# Životni ciklus: pokretanje i gašenje
# ============================================

publisher = RedisPublisher()
tailer: NginxTailer | None = None
tailer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Na startu: poveži Redis publisher i pokreni Nginx tailer.
    Na gašenju: zaustavi sve.
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
# FastAPI aplikacija
# ============================================

app = FastAPI(
    title="SIEM Ingestor",
    description="Receives raw logs from web sources and forwards to Redis.",
    version="0.1.0",
    lifespan=lifespan,
)


# ----- Provera zdravlja servisa -----

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ingestor"}


# ----- Push endpoint: prima JSON logove od aplikacija -----

@app.post("/logs", status_code=status.HTTP_202_ACCEPTED)
async def receive_log(request: Request):
    """
    Prima JSON telo i prosleđuje ga u `raw_logs` stream.

    Telo: bilo koji validan JSON objekat. Ingestor ne proverava šemu,
    to je posao Normalizera.

    Header X-Log-Source: opciono govori odakle log dolazi.
    Ako ga nema, podrazumeva se "demo-webapp".
    """
    try:
        body_bytes = await request.body()
        if not body_bytes:
            raise HTTPException(status_code=400, detail="Empty body")

        # Preserijalizuj da dobijemo čist, jednolinijski JSON string
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
            # Objavljivanje palo; vrati 503 da pozivalac može da pokuša ponovo
            raise HTTPException(status_code=503, detail="Failed to enqueue log")

        return {"accepted": True, "stream_entry_id": entry_id}

    except HTTPException:
        raise
    except Exception:
        logger.exception("receive_log_unexpected_error")
        raise HTTPException(status_code=500, detail="Internal error")


# ----- Stats endpoint (zgodno za debug) -----

@app.get("/stats")
async def stats():
    """Vraća osnovno operativno stanje ingestora."""
    return {
        "service": "ingestor",
        "redis_host": settings.redis_host,
        "redis_port": settings.redis_port,
        "nginx_tailer_enabled": settings.enable_nginx_tailer,
        "nginx_log_path": settings.nginx_access_log_path,
    }