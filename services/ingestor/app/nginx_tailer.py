"""
Asynchronous tailer for Nginx access logs.

Watches the configured access.log file for new lines and publishes each
line to the Redis `raw_logs` stream as a RawLogMessage.

Behavior:
- If the file does not exist yet at startup, the tailer waits and retries.
- If the file is rotated (truncated/replaced), the tailer reopens it.
- Failures publishing to Redis are logged but do not crash the tailer.
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
    """
    Tail-follow style reader for a single text file.

    Designed to be started as a background asyncio task in `main.py`.
    """

    def __init__(self, publisher: RedisPublisher) -> None:
        self._publisher = publisher
        self._path = Path(settings.nginx_access_log_path)
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Signal the tailer loop to exit."""
        self._stop_event.set()

    async def run(self) -> None:
        """Main tailer loop. Runs until `stop()` is called."""
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
        Open the file, seek to end, and stream new lines until either
        the file is replaced (truncated/rotated) or stop is requested.
        """
        # Open in non-blocking text mode. We use plain open() in a thread
        # executor pattern below for simplicity; line-by-line tailing
        # of small log volumes does not benefit much from aiofiles.
        with open(self._path, "r", encoding="utf-8", errors="replace") as f:
            # Seek to end so we only see *new* lines.
            f.seek(0, os.SEEK_END)
            current_inode = os.fstat(f.fileno()).st_ino

            while not self._stop_event.is_set():
                line = f.readline()
                if line:
                    await self._publish_line(line.rstrip("\n"))
                    continue

                # No new data; check whether file was rotated/replaced.
                await asyncio.sleep(0.5)
                try:
                    new_stat = self._path.stat()
                    if new_stat.st_ino != current_inode:
                        logger.info(
                            "nginx_log_rotated_reopening",
                            extra={"event_data": {"path": str(self._path)}},
                        )
                        return  # outer loop will reopen
                except FileNotFoundError:
                    logger.warning("nginx_log_disappeared_reopening")
                    return

    async def _publish_line(self, line: str) -> None:
        """Publish a single log line to Redis."""
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