# Asynchronous payment processing service example

Service that accepts a payment over HTTP, processes it asynchronously through an emulated payment gateway, and notifies the client by webhook.

| Service | URL |
|---|---|
| API + OpenAPI docs | http://localhost:8000 , http://localhost:8000/docs |
| Health probe | http://localhost:8000/health |
| RabbitMQ management | http://localhost:15672 (`guest` / `guest`) |
| PostgreSQL | `localhost:5432` (`payments` / `payments`) |

## Start app
```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Running
```bash
cp .env.example .env          # optional: adjust keys and ports
docker compose up -d --build
```

## Request examples

Both endpoints require `X-API-Key`; creation additionally requires `Idempotency-Key`.

### Create a payment

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H 'X-API-Key: dev-secret-key' \
  -H 'Idempotency-Key: order-4711-attempt-1' \
  -H 'Content-Type: application/json' \
  -d '{
        "amount": "1490.50",
        "currency": "RUB",
        "description": "Order #4711",
        "metadata": {"order_id": "4711", "user_id": 42},
        "webhook_url": "http://webhook-sink:8080/hooks/payments"
      }'
```

```http
HTTP/1.1 202 Accepted
```

```json
{
  "payment_id": "0f6b1f9e-2a4f-4c1a-9f2e-7c1a5b3d9e04",
  "status": "pending",
  "amount": "1490.5000",
  "currency": "RUB",
  "created_at": "2026-01-01T12:00:00Z",
  "idempotent_replay": false
}
```

Sending the same key with the same body returns the original `payment_id` with `idempotent_replay: true` and an `Idempotent-Replay: true` header — never a second payment. Reusing a key with a *different* body returns `409 Conflict`.

### Read a payment

```bash
curl -s http://localhost:8000/api/v1/payments/0f6b1f9e-2a4f-4c1a-9f2e-7c1a5b3d9e04 \
  -H 'X-API-Key: dev-secret-key'
```

```json
{
  "id": "0f6b1f9e-2a4f-4c1a-9f2e-7c1a5b3d9e04",
  "amount": "1490.5000",
  "currency": "RUB",
  "status": "succeeded",
  "description": "Order #4711",
  "metadata": {"order_id": "4711", "user_id": 42},
  "webhook_url": "http://webhook-sink:8080/hooks/payments",
  "created_at": "2026-01-01T12:00:00Z",
  "processed_at": "2026-01-01T12:00:03Z",
  "idempotency_key": "order-4711-attempt-1"
}
```

## Testing
```bash
uv run pytest
```