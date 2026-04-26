"""
Ingestor service configuration.

All values are read from environment variables, allowing the same
container image to run in different environments (dev/CI/prod) with
different settings.
"""

import os
from pydantic import BaseModel


class Settings(BaseModel):
    # Service binding
    host: str = os.getenv("INGESTOR_HOST", "0.0.0.0")
    port: int = int(os.getenv("INGESTOR_PORT", "8001"))

    # Redis connection
    redis_host: str = os.getenv("REDIS_HOST", "redis")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")

    # Nginx log tailing
    nginx_access_log_path: str = os.getenv(
        "NGINX_ACCESS_LOG_PATH",
        "/var/log/nginx/access.log",
    )
    enable_nginx_tailer: bool = os.getenv("ENABLE_NGINX_TAILER", "true").lower() == "true"

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()