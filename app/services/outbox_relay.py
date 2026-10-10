from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import OutboxEventRow, OutboxStatus
from app.mq_topology import (
    EVENT_HEADER_EVENT_ID,
    EVENT_HEADER_EVENT_TYPE,
    PAYMENTS_ROUTING_KEY,
    payments_exchange,
)

if TYPE_CHECKING:
    from faststream.rabbit import RabbitBroker

logger = logging.getLogger(__name__)

# Backoff schedule for repeated publish failures, in seconds.
_PUBLISH_BACKOFF_SECONDS = (1, 2, 5, 10, 30, 60)


def _backoff_for(attempts: int) -> timedelta:
    index = min(max(attempts - 1, 0), len(_PUBLISH_BACKOFF_SECONDS) - 1)
    return timedelta(seconds=_PUBLISH_BACKOFF_SECONDS[index])


class OutboxPublisher:
    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish(
        self,
        payload: dict[str, Any],
        event_id: str,
        event_type: str,
    ) -> None:
        await self._broker.publish(
            payload,
            exchange=payments_exchange,
            routing_key=PAYMENTS_ROUTING_KEY,
            headers={
                EVENT_HEADER_EVENT_ID: event_id,
                EVENT_HEADER_EVENT_TYPE: event_type,
            },
            persist=True,
            timeout=10.0,
        )


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: OutboxPublisher,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._settings = settings

    async def publish_once(self) -> int:
        published = 0
        async with self._session_factory() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(OutboxEventRow)
                        .where(
                            OutboxEventRow.status == OutboxStatus.PENDING.value,
                            OutboxEventRow.available_at <= datetime.now(UTC),
                            OutboxEventRow.attempts
                            < self._settings.outbox_max_attempts,
                        )
                        .order_by(OutboxEventRow.id)
                        .limit(self._settings.outbox_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )

            for row in rows:
                event_id = str(row.payload.get("event_id", row.id))
                try:
                    await self._publisher.publish(
                        row.payload, event_id=event_id, event_type=row.event_type
                    )
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                    row.available_at = datetime.now(UTC) + _backoff_for(row.attempts)
                    logger.warning(
                        "outbox publish failed",
                        extra={
                            "outbox_id": row.id,
                            "attempts": row.attempts,
                            "error": str(exc),
                        },
                    )
                    continue

                row.status = OutboxStatus.PUBLISHED.value
                row.published_at = datetime.now(UTC)
                row.last_error = None
                published += 1
                logger.info(
                    "outbox published",
                    extra={
                        "outbox_id": row.id,
                        "event_type": row.event_type,
                        "aggregate_id": str(row.aggregate_id),
                    },
                )

        return published

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info(
            "outbox relay started",
            extra={"batch_size": self._settings.outbox_batch_size},
        )
        try:
            while not stop_event.is_set():
                try:
                    published = await self.publish_once()
                except Exception:
                    logger.exception("outbox relay iteration failed")
                    published = 0

                if published == 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=self._settings.outbox_poll_interval,
                        )
        except asyncio.CancelledError:
            logger.info("outbox relay cancelled")
            raise
        logger.info("outbox relay stopped")


async def run() -> None:
    """Standalone relay entrypoint (`python -m app.services.outbox_relay`)."""
    from faststream.rabbit import RabbitBroker  # noqa: PLC0415

    from app.db.session import (  # noqa: PLC0415
        dispose_db_engine,
        get_db_session_factory,
    )
    from app.logging import configure_logging  # noqa: PLC0415

    configure_logging()
    settings = get_settings()

    broker = RabbitBroker(url=settings.rabbitmq_url)
    await broker.connect()
    from app.mq_topology import declare_topology  # noqa: PLC0415

    await declare_topology(broker, settings)

    relay = OutboxRelay(get_db_session_factory(), OutboxPublisher(broker), settings)
    stop_event = asyncio.Event()
    try:
        await relay.run(stop_event)
    finally:
        await broker.stop()
        await dispose_db_engine()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
