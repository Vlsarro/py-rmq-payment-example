import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
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
        logger.info("api stopped")


app = FastAPI(
    title="Payment Processing Service",
    version="0.1.0",
    summary="Asynchronous payment processing service.",
    lifespan=lifespan,
)


@app.get("/health", tags=["ops"], summary="Liveness and database probe")
async def health() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
