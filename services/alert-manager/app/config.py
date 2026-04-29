"""
Centralized configuration for the Alert Manager.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- Postgres -----
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    # ----- Redis -----
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")

    # ----- Logging -----
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ----- Dedup -----
    silence_window_seconds: int = Field(
        default=300, alias="ALERT_SILENCE_WINDOW_SECONDS",
    )

    # ----- Webhook (opt-in) -----
    webhook_url: Optional[str] = Field(default=None, alias="ALERT_WEBHOOK_URL")
    webhook_timeout_seconds: float = Field(
        default=5.0, alias="ALERT_WEBHOOK_TIMEOUT_SECONDS",
    )

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"