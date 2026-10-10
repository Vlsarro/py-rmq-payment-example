import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.router import router as payments_router
from app.config import get_settings
from app.db.session import dispose_db_engine, get_db_engine, get_db_session_factory
from app.logging import configure_logging
from app.mq_topology import declare_topology
from app.services.outbox_relay import OutboxPublisher, OutboxRelay

logger = logging.getLogger(__name__)


async def _run_relay(stop_event: asyncio.Event) -> None:
    """Connect a dedicated broker for publishing and run the relay."""
    from faststream.rabbit import RabbitBroker  # noqa: PLC0415

    settings = get_settings()
    broker = RabbitBroker(url=settings.rabbitmq_url)
    try:
        await broker.connect()
        await declare_topology(broker, settings)
        relay = OutboxRelay(get_db_session_factory(), OutboxPublisher(broker), settings)
        await relay.run(stop_event)
    except asyncio.CancelledError:
        logger.info("relay cancelled")
        raise
    except Exception:
        logger.exception("outbox relay stopped unexpectedly")
    finally:
        with contextlib.suppress(Exception):
            await broker.stop()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    logger.info("api starting", extra={"log_level": settings.log_level})

    stop_event = asyncio.Event()
    relay_task = asyncio.create_task(_run_relay(stop_event), name="outbox-relay")
    app.state.relay_task = relay_task

    try:
        yield
    finally:
        stop_event.set()
        relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay_task
        await dispose_db_engine()
        logger.info("api stopped")


app = FastAPI(
    title="Payment Processing Service",
    version="0.1.0",
    summary="Asynchronous payment processing service.",
    lifespan=lifespan,
)
app.include_router(payments_router)


@app.get("/healthz", tags=["ops"], summary="Liveness and database probe")
async def health() -> JSONResponse:
    database_ok = True
    detail = "ok"
    try:
        async with get_db_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        database_ok = False
        detail = type(exc).__name__

    return JSONResponse(
        status_code=200 if database_ok else 503,
        content={"status": "ok" if database_ok else "err", "database": detail},
    )
