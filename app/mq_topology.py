from __future__ import annotations

from faststream.rabbit import (
    Channel,
    ExchangeType,
    RabbitBroker,
    RabbitExchange,
    RabbitQueue,
)

from app.config import Settings

# --- Names -----------------------------------------------------------------
PAYMENTS_EXCHANGE = "payments"
RETRY_EXCHANGE = "payments.retry"
DLX_EXCHANGE = "payments.dlx"

PAYMENTS_QUEUE = "payments.new"
PAYMENTS_ROUTING_KEY = "payments.new"
DLQ_QUEUE = "payments.dlq"
DLQ_ROUTING_KEY = "payments.dlq"
RETRY_QUEUE_PREFIX = "payments.retry."

EVENT_HEADER_ATTEMPT = "x-attempt"
EVENT_HEADER_EVENT_ID = "x-event-id"
EVENT_HEADER_EVENT_TYPE = "x-event-type"


def retry_queue_name(attempt: int) -> str:
    return f"{RETRY_QUEUE_PREFIX}{attempt}"


def retry_routing_key(attempt: int) -> str:
    return retry_queue_name(attempt)


def has_retry_hop(attempt: int, settings: Settings) -> bool:
    return 1 <= attempt <= len(settings.retry_ttls_ms)


# --- Exchanges -------------------------------------------------------------
payments_exchange = RabbitExchange(
    name=PAYMENTS_EXCHANGE,
    type=ExchangeType.TOPIC,
    durable=True,
)
retry_exchange = RabbitExchange(
    name=RETRY_EXCHANGE,
    type=ExchangeType.DIRECT,
    durable=True,
)
dlx_exchange = RabbitExchange(
    name=DLX_EXCHANGE,
    type=ExchangeType.DIRECT,
    durable=True,
)


# --- Queues ----------------------------------------------------------------
def build_retry_queue(attempt: int, ttl_ms: int) -> RabbitQueue:
    return RabbitQueue(
        name=retry_queue_name(attempt),
        durable=True,
        routing_key=retry_routing_key(attempt),
        arguments={
            "x-message-ttl": ttl_ms,
            "x-dead-letter-exchange": PAYMENTS_EXCHANGE,
            "x-dead-letter-routing-key": PAYMENTS_ROUTING_KEY,
        },
    )


def build_payments_queue() -> RabbitQueue:
    return RabbitQueue(
        name=PAYMENTS_QUEUE,
        durable=True,
        routing_key=PAYMENTS_ROUTING_KEY,
        arguments={
            "x-dead-letter-exchange": DLX_EXCHANGE,
            "x-dead-letter-routing-key": DLQ_ROUTING_KEY,
        },
    )


dlq_queue = RabbitQueue(
    name=DLQ_QUEUE,
    durable=True,
    routing_key=DLQ_ROUTING_KEY,
)


def build_all_retry_queues(settings: Settings) -> list[RabbitQueue]:
    return [
        build_retry_queue(attempt, ttl)
        for attempt, ttl in enumerate(settings.retry_ttls_ms, start=1)
    ]


# --- Channel ---------------------------------------------------------------
def build_default_channel(settings: Settings) -> Channel:
    return Channel(prefetch_count=settings.consumer_prefetch)


async def declare_topology(broker: RabbitBroker, settings: Settings) -> None:
    payments_x = await broker.declare_exchange(payments_exchange)
    retry_x = await broker.declare_exchange(retry_exchange)
    dlx_x = await broker.declare_exchange(dlx_exchange)

    # Dead letter queue must exist before the work queue can reject into it.
    dlq = await broker.declare_queue(dlq_queue)
    await dlq.bind(dlx_x, routing_key=DLQ_ROUTING_KEY)

    work = await broker.declare_queue(build_payments_queue())
    await work.bind(payments_x, routing_key=PAYMENTS_ROUTING_KEY)

    for queue in build_all_retry_queues(settings):
        declared = await broker.declare_queue(queue)
        await declared.bind(retry_x, routing_key=queue.routing_key)
