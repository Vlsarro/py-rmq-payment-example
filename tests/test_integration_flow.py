from httpx import AsyncClient


async def test_get_payment_with_creation(
    client: AsyncClient, payment_body: dict, idempotency_key: str
) -> None:
    created = await client.post(
        "/api/v1/payments",
        json=payment_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    payment_id = created.json()["payment_id"]

    fetched = await client.get(f"/api/v1/payments/{payment_id}")

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["id"] == payment_id
    assert body["status"] == "pending"
    assert body["metadata"] == payment_body["metadata"]
    assert body["description"] == payment_body["description"]
    assert body["idempotency_key"] == idempotency_key
    assert body["processed_at"] is None
