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

## Run with docker
```bash
docker compose up --build
```

## Request examples
```bash
curl -i -X POST http://0.0.0.0:8000/api/v1/payments \
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