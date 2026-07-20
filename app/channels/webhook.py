"""Slack, Discord and generic-webhook notification channels (SPEC FR-AL-1, T20).

All three post to an incoming-webhook URL; they differ only in the JSON body and the
success status. 429/5xx raise a transient error so the caller retries.
"""

from __future__ import annotations

import httpx

from app.channels.base import ChannelError, ChannelTransientError, NotificationChannel


class _WebhookChannel(NotificationChannel):
    """Common POST-to-webhook behaviour."""

    def __init__(self, url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._url = url
        self._client = client

    def _payload(self, text: str) -> dict:
        raise NotImplementedError

    def _is_success(self, status_code: int) -> bool:
        return 200 <= status_code < 300

    async def send(self, text: str) -> None:
        owns = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            try:
                resp = await client.post(self._url, json=self._payload(text), timeout=15.0)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ChannelTransientError(f"webhook request failed: {exc}") from exc

            if resp.status_code == 429 or resp.status_code >= 500:
                raise ChannelTransientError(f"webhook status {resp.status_code}")
            if not self._is_success(resp.status_code):
                raise ChannelError(f"webhook status {resp.status_code}: {resp.text[:200]}")
        finally:
            if owns:
                await client.aclose()


class SlackChannel(_WebhookChannel):
    def _payload(self, text: str) -> dict:
        return {"text": text}


class DiscordChannel(_WebhookChannel):
    def _payload(self, text: str) -> dict:
        return {"content": text}


class GenericWebhookChannel(_WebhookChannel):
    def _payload(self, text: str) -> dict:
        return {"text": text}
