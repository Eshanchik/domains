"""Telegram notification channel (SPEC FR-AL-1, MVP).

One global bot (token from Settings); each channel targets a chat_id. Sends via the
Bot API ``sendMessage``. 429/5xx raise a transient error so the caller can retry.
"""

from __future__ import annotations

import httpx

from app.channels.base import ChannelError, ChannelTransientError, NotificationChannel

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramChannel(NotificationChannel):
    def __init__(
        self, bot_token: str, chat_id: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._client = client

    async def send(self, text: str) -> None:
        owns = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            try:
                resp = await client.post(
                    API_URL.format(token=self._token),
                    json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
                    timeout=15.0,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ChannelTransientError(f"telegram request failed: {exc}") from exc

            if resp.status_code == 429 or resp.status_code >= 500:
                raise ChannelTransientError(f"telegram status {resp.status_code}")
            if resp.status_code >= 400:
                raise ChannelError(f"telegram status {resp.status_code}: {resp.text[:200]}")
        finally:
            if owns:
                await client.aclose()
