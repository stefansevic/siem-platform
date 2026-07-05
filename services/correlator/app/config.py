"""
Centralizovana konfiguracija Correlator servisa.

"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- Redis -----
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")

    # ----- Logging -----
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ----- Pragovi korelacionih pravila -----
    # Podrazumevane vrednosti prate projektnu specifikaciju.
    brute_force_threshold: int = Field(default=5, alias="BRUTE_FORCE_THRESHOLD")
    brute_force_window_seconds: int = Field(
        default=60, alias="BRUTE_FORCE_WINDOW_SECONDS"
    )
    dir_scan_threshold: int = Field(default=20, alias="DIR_SCAN_THRESHOLD")
    dir_scan_window_seconds: int = Field(
        default=60, alias="DIR_SCAN_WINDOW_SECONDS"
    )
    ato_failure_threshold: int = Field(default=5, alias="ATO_FAILURE_THRESHOLD")
    ato_window_seconds: int = Field(default=600, alias="ATO_WINDOW_SECONDS")

    # ----- Convenience -----
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"