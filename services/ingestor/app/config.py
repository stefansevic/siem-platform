"""
Konfiguracija Ingestor servisa.

Podesavanja za port, Redis konekciju i putanju do Nginx access log-a.
"""

import os
from pydantic import BaseModel


class Settings(BaseModel):
    # Adresa i port servisa
    host: str = os.getenv("INGESTOR_HOST", "0.0.0.0")
    port: int = int(os.getenv("INGESTOR_PORT", "8001"))

    # Konekcija ka Redis-u
    redis_host: str = os.getenv("REDIS_HOST", "redis")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")

    # Nginx log koji tailer prati
    nginx_access_log_path: str = os.getenv(
        "NGINX_ACCESS_LOG_PATH",
        "/var/log/nginx/access.log",
    )
    # Perzistentni offset tailera (na zapisivom volume-u): pamti dokle je
    # pročitano, da posle restarta ne preskoči linije nastale dok nije radio.
    nginx_offset_path: str = os.getenv(
        "NGINX_OFFSET_PATH",
        "/var/lib/ingestor/nginx_access.offset",
    )
    enable_nginx_tailer: bool = os.getenv("ENABLE_NGINX_TAILER", "true").lower() == "true"

    # Logovanje
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()