from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from faststream import AckPolicy, FastStream
from faststream.rabbit import RabbitBroker, RabbitMessage
from sqlalchemy import select

from app.api.schemas import PaymentCreatedEvent
from app.config import Settings, get_settings
from app.db.models import Currency, Payment, PaymentStatus
from app.db.session import dispose_db_engine, get_db_session_factory
from app.logging import configure_logging
from app.mq_topology import (
    EVENT_HEADER_ATTEMPT,
    PAYMENTS_QUEUE,
    build_default_channel,
    build_payments_queue,
    declare_topology,
    dlq_queue,
    dlx_exchange,
    has_retry_hop,
    payments_exchange,
    retry_exchange,
    retry_routing_key,
)
from app.services.fake_payment_gateway import GatewayDeclined, PaymentGateway
from app.services.webhook_client import WebhookClient, WebhookDeliveryError

logger = logging.getLogger(__name__)

settings = get_settings()
broker = RabbitBroker(
    url=settings.rabbitmq_url,
    ack_policy=AckPolicy.MANUAL,
    default_channel=build_default_channel(settings),
)
app = FastStream(broker)

_BROKER_OWNED_HEADERS = frozenset(
    {
        "x-death",
        "x-first-death-exchange",
        "x-first-death-queue",
        "x-first-death-reason",
        "x-first-death-routing-key",
    }
)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class PaymentProcessor:
    def __init__(self, gateway: PaymentGateway, webhooks: WebhookClient) -> None:
        self._gateway = gateway
        self._webhooks = webhooks

    async def charge(self, payment: Payment) -> None:
        result = await self._gateway.charge(
            payment.amount,
            Currency(payment.currency),
            payment.idempotency_key,
        )
        payment.status = result.status.value
        payment.processed_at = datetime.now(UTC)

    async def notify(self, payment: Payment) -> None:
        await self._webhooks.deliver(payment)


class Resources:
    def __init__(self, settings_: Settings | None) -> None:
        self.settings = settings_ or get_settings()
        self.gateway = PaymentGateway(self.settings)
        self.webhooks = WebhookClient(self.settings)
        self.processor = PaymentProcessor(self.gateway, self.webhooks)

    async def aclose(self) -> None:
        await self.webhooks.aclose()


_resources: Resources | None = None


def get_resources() -> Resources:
    global _resources
    if _resources is None:
        _resources = Resources(settings)
    return _resources


# ---------------------------------------------------------------------------
# Retry plumbing
# ---------------------------------------------------------------------------


def attempt_from_headers(message: RabbitMessage) -> int:
    raw = message.headers.get(EVENT_HEADER_ATTEMPT, 1)
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 1


async def requeue_or_reject(
    message: RabbitMessage, event: PaymentCreatedEvent, attempt: int
) -> None:
    if attempt < settings.retry_max_attempts and has_retry_hop(attempt, settings):
        await requeue(message, event, attempt)
    else:
        await message.reject(requeue=False)


async def requeue(
    message: RabbitMessage, event: PaymentCreatedEvent, attempt: int
) -> None:
    next_attempt = attempt + 1
    await broker.publish(
        event.model_dump(mode="json"),
        exchange=retry_exchange,
        routing_key=retry_routing_key(attempt),
        headers={
            key: value
            for key, value in message.headers.items()
            if key not in _BROKER_OWNED_HEADERS
        }
        | {EVENT_HEADER_ATTEMPT: next_attempt},
        persist=True,
        timeout=10.0,
    )
    await message.ack()
    logger.info(
        "message requeued for retry",
        extra={
            "payment_id": str(event.payment_id),
            "attempt": attempt,
            "next_attempt": next_attempt,
        },
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@broker.subscriber(
    build_payments_queue(),
    payments_exchange,
    ack_policy=AckPolicy.MANUAL,
)
async def handle_payment_created(
    event: PaymentCreatedEvent,
    message: RabbitMessage,
) -> None:
    attempt = attempt_from_headers(message)
    processor = get_resources().processor
    session_factory = get_db_session_factory()
    payment_id = event.payment_id

    exhausted = False

    try:
        async with session_factory() as session, session.begin():
            payment = (
                await session.execute(
                    select(Payment).where(Payment.id == payment_id).with_for_update()
                )
            ).scalar_one_or_none()

            if payment is None:
                logger.warning(
                    "payment missing for event",
                    extra={"payment_id": str(payment_id)},
                )
                await message.ack()
                return

            if payment.status == PaymentStatus.PENDING.value:
                try:
                    await processor.charge(payment)
                except GatewayDeclined as exc:
                    if attempt >= settings.retry_max_attempts:
                        payment.status = PaymentStatus.FAILED.value
                        payment.processed_at = datetime.now(UTC)
                        exhausted = True
                        logger.warning(
                            "gateway declined; marking failed",
                            extra={
                                "payment_id": str(payment_id),
                                "attempt": attempt,
                            },
                        )
                    else:
                        logger.info(
                            "gateway declined; requeueing",
                            extra={
                                "payment_id": str(payment_id),
                                "attempt": attempt,
                                "error": str(exc),
                            },
                        )
                        await requeue(message, event, attempt)
                        return
            else:
                logger.info(
                    "payment already terminal; notifying only",
                    extra={
                        "payment_id": str(payment_id),
                        "status": payment.status,
                        "attempt": attempt,
                    },
                )

        async with session_factory() as session:
            payment = (
                await session.execute(select(Payment).where(Payment.id == payment_id))
            ).scalar_one()
            status = payment.status
            try:
                await processor.notify(payment)
            except WebhookDeliveryError as exc:
                logger.warning(
                    "webhook delivery failed",
                    extra={
                        "payment_id": str(payment_id),
                        "attempt": attempt,
                        "error": str(exc),
                    },
                )
                await requeue_or_reject(message, event, attempt)
                return

        logger.info(
            "payment processed",
            extra={
                "payment_id": str(payment_id),
                "status": status,
                "attempt": attempt,
            },
        )
        if exhausted:
            await message.reject(requeue=False)
        else:
            await message.ack()

    except Exception:
        logger.exception(
            "unhandled error",
            extra={"payment_id": str(payment_id), "attempt": attempt},
        )
        with contextlib.suppress(Exception):
            await requeue_or_reject(message, event, attempt)
        raise


async def handle_dead_letter(
    event: PaymentCreatedEvent,
    message: RabbitMessage,
) -> None:
    deaths: list[dict[str, Any]] = message.headers.get("x-death") or []
    logger.warning(
        "message dead-lettered",
        extra={
            "payment_id": str(event.payment_id),
            "reason": deaths[0].get("reason") if deaths else None,
            "attempts": attempt_from_headers(message),
        },
    )
    await message.ack()


if settings.dlq_consumer_enabled:
    broker.subscriber(
        dlq_queue,
        dlx_exchange,
        ack_policy=AckPolicy.MANUAL,
    )(handle_dead_letter)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@app.after_startup
async def _on_startup() -> None:
    await declare_topology(broker, settings)
    get_resources()  # build the gateway and the HTTP client up front
    logger.info(
        "consumer started",
        extra={
            "queue": PAYMENTS_QUEUE,
            "max_attempts": settings.retry_max_attempts,
            "retry_queue_count": len(settings.retry_ttls_ms),
            "prefetch": settings.consumer_prefetch,
        },
    )


@app.after_shutdown
async def _on_shutdown() -> None:
    if _resources is not None:
        await _resources.aclose()
    await dispose_db_engine()


def main() -> None:
    """Entry point for `python -m app.services.payments_processor`."""
    configure_logging()
    logger.info(
        "starting consumer",
        extra={"dlq_consumer_enabled": settings.dlq_consumer_enabled},
    )
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
