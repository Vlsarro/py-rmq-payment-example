from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    log_level: str = "INFO"
    api_key: str = "dev-secret-key"
    webhook_secret: str = "dev-webhook-secret"

    # --- Infrastructure ---------------------------------------------------
    database_url: str = "postgresql+asyncpg://payments:payments@postgres:5432/payments"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
