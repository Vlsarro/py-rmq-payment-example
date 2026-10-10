from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import IdempotencyConflictError, PaymentNotFoundError
from app.api.schemas import PaymentCreatedEvent, PaymentCreateRequest
from app.db.models import OutboxEvent, OutboxEventRow, Payment, PaymentStatus

_EVENT_FIELDS = (
    "id",
    "amount",
    "currency",
    "webhook_url",
)


@dataclass(frozen=True, slots=True)
class CreatePaymentResult:
    payment: Payment
    idempotent_replay: bool


def fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def create_payment(
    session: AsyncSession,
    request: PaymentCreateRequest,
    idempotency_key: str,
) -> CreatePaymentResult:
    body = request.canonical_payload()
    request_fp = fingerprint(body)

    stmt = (
        pg_insert(Payment)
        .values(
            id=uuid.uuid4(),
            amount=request.amount,
            currency=request.currency.value,
            status=PaymentStatus.PENDING.value,
            description=request.description,
            meta=request.metadata,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fp,
            webhook_url=str(request.webhook_url),
        )
        .on_conflict_do_nothing(index_elements=[Payment.idempotency_key])
        .returning(Payment.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()

    if inserted_id is None:
        existing = await get_payment_by_key(session, idempotency_key)
        if existing is None:  # pragma: no cover - only on a concurrent delete
            raise IdempotencyConflictError(idempotency_key)
        if existing.request_fingerprint != request_fp:
            raise IdempotencyConflictError(idempotency_key)
        return CreatePaymentResult(payment=existing, idempotent_replay=True)

    payment = await get_payment(session, inserted_id)
    if not payment:
        raise PaymentNotFoundError(inserted_id)

    session.add(
        OutboxEventRow(
            aggregate_id=payment.id,
            event_type=OutboxEvent.PAYMENT_CREATED.value,
            payload=build_event_payload(payment),
        )
    )
    await session.commit()
    await session.refresh(payment)
    return CreatePaymentResult(payment=payment, idempotent_replay=False)


def build_event_payload(payment: Payment) -> dict[str, Any]:
    fields = {name: getattr(payment, name) for name in _EVENT_FIELDS}
    event = PaymentCreatedEvent(
        event_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        **fields,
    )
    return event.model_dump(mode="json")


async def get_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    result = await session.execute(select(Payment).where(Payment.id == payment_id))
    return result.scalar_one_or_none()


async def get_payment_by_key(
    session: AsyncSession, idempotency_key: str
) -> Payment | None:
    """Return a payment by idempotency key, or None."""
    result = await session.execute(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def get_required_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment:
    payment = await get_payment(session, payment_id)
    if payment is None:
        raise PaymentNotFoundError(payment_id)
    return payment


def normalize_amount(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.0001"))
