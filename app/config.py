from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    consumer_prefetch: int = 4

    # --- Webhook delivery -------------------------------------------------
    webhook_timeout: float = 10.0
    webhook_allowed_hosts: Annotated[frozenset[str], NoDecode] = Field(
        default_factory=frozenset
    )

    @field_validator("webhook_allowed_hosts", mode="before")
    @classmethod
    def parse_hosts(cls, value):
        if value is None:
            return frozenset()

        if isinstance(value, str):
            hosts = (host.strip() for host in value.split(","))
            return frozenset(host for host in hosts if host)

        return value

    # --- Payment gateway emulator ----------------------------------------
    gateway_success_rate: float = Field(default=0.9, gt=0.0, le=1.0)
    gateway_min_delay: float = Field(default=2.0, ge=0.0)
    gateway_max_delay: float = Field(default=5.0, ge=0.0)

    # --- Outbox relay -----------------------------------------------------
    outbox_poll_interval: float = Field(default=0.5, gt=0.0)
    outbox_batch_size: int = Field(default=50, gt=0)
    outbox_max_attempts: int = Field(default=10, gt=0)

    # --- Retry / DLQ ------------------------------------------------------
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_base_ttl_ms: int = Field(default=5_000, gt=0)
    retry_ttl_ms: int = Field(default=30_000, gt=0)

    # --- Observability ----------------------------------------------------
    dlq_consumer_enabled: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retry_ttls_ms(self) -> list[int]:
        """Exponential backoff TTLs, one per retry hop.

        Three attempts means two retry hops, so the plan yields
        ``[retry_base_ttl_ms, retry_ttl_ms]`` -> ``[5000, 30000]``.
        """
        hops = self.retry_max_attempts - 1
        if hops <= 0:
            return []
        if hops == 1:
            return [self.retry_base_ttl_ms]
        # Geometric progression ending exactly on ``retry_ttl_ms``.
        ratio = (self.retry_ttl_ms / self.retry_base_ttl_ms) ** (1 / (hops - 1))
        return [round(self.retry_base_ttl_ms * ratio**i) for i in range(hops)]

    def is_host_allowed(self, host: str) -> bool:
        if not self.webhook_allowed_hosts:
            # An empty allowlist permits any host, which keeps local development
            # friction-free. Configure the variable to lock webhooks down.
            return True
        return host.lower() in {h.lower() for h in self.webhook_allowed_hosts}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
