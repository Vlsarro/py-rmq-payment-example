from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.db.models import Currency, PaymentStatus

Amount = Annotated[
    Decimal,
    Field(
        gt=0,
        max_digits=18,
        decimal_places=4,
        description="Positive amount, max 18 digits with 4 decimal places.",
        examples=["1490.50"],
    ),
]

IdempotencyKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=255,
        description="Client-supplied key that makes payment creation idempotent.",
        examples=["order-4711-attempt-1"],
    ),
]


AMOUNT_SCALE = Decimal("0.0001")


class PaymentCreateRequest(BaseModel):
    """Body of ``POST /api/v1/payments``."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "amount": "1490.50",
                    "currency": "RUB",
                    "description": "Order #4711",
                    "metadata": {"order_id": "4711", "user_id": 42},
                    "webhook_url": "http://webhook-sink:8080/hooks/payments",
                }
            ]
        },
    )

    amount: Amount
    currency: Currency
    description: str | None = Field(default=None, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: HttpUrl

    @field_validator("metadata")
    @classmethod
    def _metadata_must_be_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        # JSONB accepts scalars, but the spec defines metadata as a JSON object
        # carrying additional key/value information.
        if not isinstance(value, dict):  # pragma: no cover - pydantic enforces
            raise ValueError("metadata must be a JSON object")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        """Return the request in a form suitable for fingerprinting."""
        return {
            # Quantised to the column's scale, so 1490.5 and 1490.50 are the
            # same request and hash alike.
            "amount": format(self.amount.quantize(AMOUNT_SCALE), "f"),
            "currency": self.currency.value,
            "description": self.description,
            "metadata": self.metadata,
            "webhook_url": str(self.webhook_url),
        }


class PaymentAcceptedResponse(BaseModel):
    """Response of `POST /api/v1/payments`."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "payment_id": "0f6b1f9e-2a4f-4c1a-9f2e-7c1a5b3d9e04",
                    "status": "pending",
                    "amount": "1490.5000",
                    "currency": "RUB",
                    "created_at": "2026-01-01T12:00:00+00:00",
                    "idempotent_replay": False,
                }
            ]
        }
    )

    payment_id: uuid.UUID
    status: PaymentStatus
    amount: Decimal
    currency: Currency
    created_at: datetime
    # True when the key was seen before and the original payment is returned.
    idempotent_replay: bool = False


class PaymentResponse(BaseModel):
    """Response of `GET /api/v1/payments/{payment_id}`."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    amount: Decimal
    currency: Currency
    status: PaymentStatus
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None = None
    idempotency_key: str


class PaymentCreatedEvent(BaseModel):
    """Message body published to ``payments.new`` by the outbox relay."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: uuid.UUID
    event_type: str = "payment.created"
    occurred_at: datetime
    payment_id: uuid.UUID
    amount: Decimal
    currency: Currency
    webhook_url: str
