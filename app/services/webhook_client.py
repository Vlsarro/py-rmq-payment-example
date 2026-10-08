import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.db.models import Payment


class WebhookDeliveryError(RuntimeError):
    """The webhook endpoint did not accept the notification.

    Retryable: the consumer requeues the message rather than discarding it,
    and dead-letters it once the attempt budget is exhausted.
    """


class WebhookBlockedError(WebhookDeliveryError):
    """The target host is not on the configured allowlist."""


def build_payload(payment: Payment) -> dict[str, Any]:
    return {
        "event": "payment.processed",
        "payment_id": str(payment.id),
        "status": payment.status,
        "amount": format(payment.amount, "f"),
        "currency": payment.currency,
        "processed_at": payment.processed_at.isoformat()
        if payment.processed_at
        else None,
    }


def sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(body: bytes, secret: str, signature: str) -> bool:
    return hmac.compare_digest(sign(body, secret), signature)


class WebhookClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._secret = settings.webhook_secret
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.webhook_timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def deliver(self, payment: Payment) -> None:
        self._assert_host_allowed(payment.webhook_url)

        body = json.dumps(build_payload(payment), separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Signature": sign(body, self._secret),
            "X-Payment-Id": str(payment.id),
        }

        try:
            response = await self._client.post(
                payment.webhook_url, content=body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(
                f"Webhook request to {payment.webhook_url} failed: {exc}"
            ) from exc

        if not 200 <= response.status_code < 300:
            raise WebhookDeliveryError(
                f"Webhook {payment.webhook_url} returned HTTP {response.status_code}"
            )

    def _assert_host_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise WebhookBlockedError(
                f"Unsupported webhook scheme {parsed.scheme!r} in {url!r}"
            )
        if not self._settings.is_host_allowed(parsed.hostname or ""):
            raise WebhookBlockedError(
                f"Webhook host {parsed.hostname!r} is not in WEBHOOK_ALLOWED_HOSTS"
            )
