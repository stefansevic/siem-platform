"""
Centralized configuration for the API Gateway.
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

    # ----- Postgres -----
    postgres_user: str = Field(alias="POSTGRES_USER")
    postgres_password: str = Field(alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(alias="POSTGRES_DB")
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    # ----- HTTP server -----
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8005, alias="API_PORT")

    # ----- CORS — frontend will run on a different origin during dev -----
    cors_allow_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        alias="API_CORS_ALLOW_ORIGINS",
    )

    # ----- Logging -----
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]