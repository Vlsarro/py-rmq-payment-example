import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Webhook sink")

# Set to a number to make the next N requests fail with HTTP 500, which forces
# the consumer down its retry path.
FAIL_NEXT = int(os.getenv("SINK_FAIL_NEXT", "0"))
_seen: list[dict[str, Any]] = []


@app.post("/hooks/payments")
async def receive(request: Request) -> Response:
    """Record a payment notification."""
    global FAIL_NEXT

    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"_raw": body.decode("utf-8", "replace")}

    _seen.append(
        {
            "received_at": datetime.now(UTC).isoformat(),
            "signature": request.headers.get("x-signature"),
            "payload": payload,
        }
    )
    print(f"[sink] received #{len(_seen)}: {json.dumps(payload)}", flush=True)

    if FAIL_NEXT > 0:
        FAIL_NEXT -= 1
        print(f"[sink] deliberately failing ({FAIL_NEXT} failures left)", flush=True)
        return Response(status_code=500, content="intentional failure")

    return Response(status_code=200, content="ok")


@app.get("/hooks/payments")
async def received() -> dict[str, Any]:
    return {"count": len(_seen), "events": _seen}


@app.post("/hooks/payments/fail-next/{count}")
async def fail_next(count: int) -> dict[str, str]:
    global FAIL_NEXT  # noqa: PLW0603
    FAIL_NEXT = max(count, 0)
    return {"status": "armed", "fail_next": str(FAIL_NEXT)}
