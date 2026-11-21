"""Application settings - every value comes from the environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings read from the process environment (and ``.env`` in dev)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["dev", "test", "prod"] = "dev"

    session_secret: str = Field(min_length=16)
    fernet_key: str = Field(min_length=32)

    database_url: str
    scraper_url: str = "http://scraper:8080"

    hunter_daily_limit: int = Field(default=20, ge=1, le=500)

    meta_api_version: str = "v27.0"
    meta_system_token: str = ""

    admin_email: str = "admin@upsidemkt.com.br"
    admin_password: str = ""

    tz: str = "America/Sao_Paulo"

    session_cookie_name: str = "upside_session"
    session_max_age_seconds: int = Field(default=60 * 60 * 24 * 14, ge=300)

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            msg = "DATABASE_URL must use the postgresql+psycopg:// driver"
            raise ValueError(msg)
        return value

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def sync_database_url(self) -> str:
        """Same database, for tools that need a blocking connection (Alembic)."""
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings singleton. Cached so the environment is read once per process."""
    return Settings()  # pyright: ignore[reportCallIssue]
