import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient

TEST_API_KEY = "dev-secret-key"


@pytest.fixture
def payment_body() -> dict:
    return {
        "amount": "1490.50",
        "currency": "RUB",
        "description": "Order #4711",
        "metadata": {"order_id": "4711"},
        "webhook_url": "http://webhook-sink:8080/hooks/payments",
    }


@pytest.fixture
def api_key() -> str:
    return TEST_API_KEY


@pytest_asyncio.fixture
async def client(
    api_key: str,
) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        base_url="http://0.0.0.0:8000",
        headers={"X-API-Key": api_key},
    ) as http_client:
        yield http_client


@pytest.fixture
def idempotency_key() -> str:
    return f"test-key-{uuid.uuid4()}"
