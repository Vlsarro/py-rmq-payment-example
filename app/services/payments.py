from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PaymentCreateRequest
from app.db.models import Payment


@dataclass(frozen=True, slots=True)
class CreatePaymentResult:
    """Outcome of a create call, whether fresh or replayed."""

    payment: Payment
    idempotent_replay: bool


def fingerprint(payload: dict[str, Any]) -> str:
    """Hash a canonicalised request body for exact replay comparison."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def create_payment(
    session: AsyncSession,
    request: PaymentCreateRequest,
    idempotency_key: str,
) -> CreatePaymentResult:
    raise NotImplementedError()


async def get_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    """Return a payment by id, or None."""
    result = await session.execute(select(Payment).where(Payment.id == payment_id))
    return result.scalar_one_or_none()
