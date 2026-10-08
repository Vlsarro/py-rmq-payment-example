from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.config import Settings
from app.db.models import Currency, PaymentStatus


class GatewayError(RuntimeError):
    """Base class for failures reported by the emulated gateway."""


class GatewayDeclined(GatewayError):
    """The gateway refused the payment. Retryable: it fails at random."""


@dataclass(frozen=True, slots=True)
class GatewayResult:
    status: PaymentStatus
    transaction_id: str


class PaymentGateway:
    """Fake gateway used in place of a real payment provider."""

    def __init__(
        self,
        settings: Settings,
        *,
        rng: random.Random | None = None,
        sleeper: object = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._rng = rng or random.Random()
        self._sleep = sleeper

    async def charge(
        self,
        amount: Decimal,
        currency: Currency,
        idempotency_key: str,
    ) -> GatewayResult:
        delay = self._rng.uniform(
            self._settings.gateway_min_delay, self._settings.gateway_max_delay
        )
        await asyncio.sleep(delay)

        if self._rng.random() >= self._settings.gateway_success_rate:
            raise GatewayDeclined(
                f"Gateway declined {amount} {currency.value} "
                f"(idempotency_key={idempotency_key})"
            )

        return GatewayResult(
            status=PaymentStatus.SUCCEEDED,
            transaction_id=uuid.uuid4().hex,
        )
