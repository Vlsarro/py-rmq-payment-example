from __future__ import annotations

import uuid

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class IdempotencyConflictError(Exception):
    """An idempotency key was reused with a different request body."""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"Idempotency-Key {idempotency_key!r} was already used "
            "with a different request body"
        )


class PaymentNotFoundError(Exception):
    """No payment exists for the requested identifier."""

    def __init__(self, payment_id: uuid.UUID) -> None:
        self.payment_id = payment_id
        super().__init__(f"Payment {payment_id} not found")


async def idempotency_conflict_handler(
    request: Request, exc: IdempotencyConflictError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc), "idempotency_key": exc.idempotency_key},
    )


async def payment_not_found_handler(
    request: Request, exc: PaymentNotFoundError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc), "payment_id": str(exc.payment_id)},
    )


def not_found(payment_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Payment {payment_id} not found",
    )
