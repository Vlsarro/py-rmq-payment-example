from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from app.api.dependencies import SessionDep, verify_api_key
from app.api.errors import not_found
from app.api.schemas import (
    IdempotencyKey,
    PaymentAcceptedResponse,
    PaymentCreateRequest,
    PaymentResponse,
)
from app.db.models import PaymentStatus
from app.services.payments import create_payment, get_payment

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(verify_api_key)],
)


# FIXME: strenum errs


@router.post(
    "",
    response_model=PaymentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a payment",
    responses={
        200: {"description": "Idempotent replay: the key was already used."},
        401: {"description": "Missing or invalid X-API-Key."},
        409: {"description": "Idempotency-Key reused with a different body."},
        422: {"description": "Request validation failed."},
    },
)
async def create_payment_handler(
    payload: PaymentCreateRequest,
    session: SessionDep,
    response: Response,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
) -> PaymentAcceptedResponse:
    result = await create_payment(session, payload, idempotency_key)

    if result.idempotent_replay:
        # The spec fixes 202 for creation; a replay is a success too, so keep
        # the status code and advertise the replay through the body instead.
        response.headers["Idempotent-Replay"] = "true"
        logger.info(
            "idempotent replay",
            extra={"payment_id": str(result.payment.id)},
        )

    return PaymentAcceptedResponse(
        payment_id=result.payment.id,
        status=PaymentStatus(result.payment.status),
        amount=result.payment.amount,
        currency=result.payment.currency,
        created_at=result.payment.created_at,
        idempotent_replay=result.idempotent_replay,
    )


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    summary="Get payment details",
    responses={
        401: {"description": "Missing or invalid X-API-Key."},
        404: {"description": "Unknown payment_id."},
    },
)
async def get_payment_handler(
    payment_id: uuid.UUID,
    session: SessionDep,
) -> PaymentResponse:
    payment = await get_payment(session, payment_id)
    if payment is None:
        raise not_found(payment_id)
    return PaymentResponse(
        id=payment.id,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        description=payment.description,
        metadata=payment.meta,
        webhook_url=payment.webhook_url,
        created_at=payment.created_at,
        processed_at=payment.processed_at,
        idempotency_key=payment.idempotency_key,
    )
