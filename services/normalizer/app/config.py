"""
Centralizovana konfiguracija iz environment varijabli.

Pydantic Settings proverava tipove na startu, pa nedostajuća ili
pogrešna env varijabla odmah obori servis sa jasnom porukom, umesto
da pukne usred rada sa nejasnim NameError ili KeyError.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Dozvoli da druge Postgres/Redis varijable iz zajedničkog .env
        # postoje, bez greške na nepoznate ključeve.
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

    # ----- Convenience -----
    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ----- Elasticsearch (dual-write za pretragu događaja) -----
    elasticsearch_url: str = Field(
        default="http://elasticsearch:9200",
        alias="ELASTICSEARCH_URL",
    )
    # Ako je True, greška u upisu u ES preskoči događaj iz ES-a, ali se
    # on i dalje upiše u Postgres. Ako je False, ES greške se samo
    # loguju - Postgres ostaje source of truth.
    elasticsearch_required: bool = Field(
        default=False,
        alias="ELASTICSEARCH_REQUIRED",
    )