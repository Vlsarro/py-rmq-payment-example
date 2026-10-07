import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db.session import dispose_db_engine, get_db_engine
from app.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    logger.info("api starting", extra={"log_level": settings.log_level})

    stop_event = asyncio.Event()

    try:
        yield
    finally:
        stop_event.set()
        await dispose_db_engine()
        logger.info("api stopped")


app = FastAPI(
    title="Payment Processing Service",
    version="0.1.0",
    summary="Asynchronous payment processing service.",
    lifespan=lifespan,
)


@app.get("/health", tags=["ops"], summary="Liveness and database probe")
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
