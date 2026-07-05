"""
Tailer za Nginx access log.

Prati access.log fajl i svaku novu liniju objavljuje u Redis stream
`raw_logs` kao RawLogMessage. Ovo je PULL grana ingestora.

Ponašanje:
- Ako fajl još ne postoji na startu, tailer čeka i pokušava ponovo.
- Greška pri slanju u Redis se loguje, ali ne ruši tailer.
"""

import asyncio
import logging
import os
from pathlib import Path

from shared.ecs_models import LogFormat, LogSource, RawLogMessage

from .config import settings
from .redis_publisher import RedisPublisher

logger = logging.getLogger(__name__)


class NginxTailer:

    # Citac tekstualnog fajla koji se stalno dopunjuje 

    def __init__(self, publisher: RedisPublisher) -> None:
        self._publisher = publisher
        self._path = Path(settings.nginx_access_log_path)
        self._stop_event = asyncio.Event()


    def stop(self) -> None:
        """Javi petlji tailera da izađe."""
        self._stop_event.set()


    async def run(self) -> None:
        # Glavna petlja tailera. Radi dok se ne pozove `stop()`
        logger.info(
            "nginx_tailer_started",
            extra={"event_data": {"path": str(self._path)}},
        )

        while not self._stop_event.is_set():
            try:
                await self._tail_once()
            except FileNotFoundError:
                logger.warning(
                    "nginx_log_not_found_retrying",
                    extra={"event_data": {"path": str(self._path)}},
                )
                await asyncio.sleep(2.0)
            except Exception:
                logger.exception("nginx_tailer_unexpected_error")
                await asyncio.sleep(2.0)

        logger.info("nginx_tailer_stopped")


    async def _tail_once(self) -> None:
        """
        Otvori fajl, skoči na kraj i čitaj nove linije dok se ne zatraži zaustavljanje.
        """

        with open(self._path, "r", encoding="utf-8", errors="replace") as f:
            # Skoči na kraj, da vidimo samo NOVE linije (ne ceo istorijski log)
            f.seek(0, os.SEEK_END)
            # Zapamti inode fajla da kasnije prepoznamo rotaciju
            current_inode = os.fstat(f.fileno()).st_ino

            while not self._stop_event.is_set():
                line = f.readline()
                if line:
                    await self._publish_line(line.rstrip("\n"))
                    continue

                # Nema novih linija; proveri da nije fajl rotiran/zamenjen.
                await asyncio.sleep(0.5)
                try:
                    new_stat = self._path.stat()
                    # Isti naziv, drugi inode = fajl je zamenjen (rotacija)
                    if new_stat.st_ino != current_inode:
                        logger.info(
                            "nginx_log_rotated_reopening",
                            extra={"event_data": {"path": str(self._path)}},
                        )
                        return  
                except FileNotFoundError:
                    logger.warning("nginx_log_disappeared_reopening")
                    return


    async def _publish_line(self, line: str) -> None:
        """Objavi jednu log liniju u Redis."""
        if not line.strip():
            return

        message = RawLogMessage(
            source=LogSource.NGINX,
            format=LogFormat.NGINX_COMBINED,
            payload=line,
            origin=str(self._path),
        )
        entry_id = await self._publisher.publish_raw_log(message)
        if entry_id:
            logger.debug(
                "nginx_line_published",
                extra={"event_data": {"entry_id": entry_id}},
            )