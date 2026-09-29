"""Slack, Discord and generic-webhook notification channels (SPEC FR-AL-1, T20).

All three post to an incoming-webhook URL; they differ only in the JSON body and the
success status. 429/5xx raise a transient error so the caller retries.
"""

from __future__ import annotations

import re

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

    async def _post(self, payload: dict) -> None:
        owns = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            try:
                resp = await client.post(self._url, json=payload, timeout=15.0)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ChannelTransientError(f"webhook request failed: {exc}") from exc

            if resp.status_code == 429 or resp.status_code >= 500:
                raise ChannelTransientError(f"webhook status {resp.status_code}")
            if not self._is_success(resp.status_code):
                raise ChannelError(f"webhook status {resp.status_code}: {resp.text[:200]}")
        finally:
            if owns:
                await client.aclose()

    async def _send_one(self, text: str) -> None:
        await self._post(self._payload(text))


class SlackChannel(_WebhookChannel):
    def _payload(self, text: str) -> dict:
        return {"text": text}

    async def send_alert(self, message: object) -> None:
        from app.services.alert_message import render_plain
        from app.services.people import slack_escape

        await self.send(slack_escape(render_plain(message)))  # no <!channel> from data


_DISCORD_USER = re.compile(r"<@!?(\d{15,22})>")
_DISCORD_ROLE = re.compile(r"<@&(\d{15,22})>")


def discord_allowed_mentions(content: str) -> dict:
    """Ping exactly the users/roles written as ``<@id>`` / ``<@&id>`` in ``content``.

    ``parse: []`` disables implicit parsing, so ``@everyone`` / ``@here`` typed into a
    comment (or a domain name that looks like one) can never mass-ping the channel.
    """
    users = list(dict.fromkeys(_DISCORD_USER.findall(content or "")))[:100]
    roles = list(dict.fromkeys(_DISCORD_ROLE.findall(content or "")))[:100]
    return {"parse": [], "users": users, "roles": roles}


class DiscordChannel(_WebhookChannel):
    MAX_LEN = 2000  # Discord webhook "content" hard limit

    def _payload(self, text: str) -> dict:
        return {"content": text, "allowed_mentions": discord_allowed_mentions(text)}

    async def send_digest(self, digest: object) -> None:
        """Render the digest as native Discord embeds (colored severity cards)."""
        from app.services.digest import render_discord

        for body in render_discord(digest):
            body["allowed_mentions"] = discord_allowed_mentions(body.get("content", ""))
            await self._post(body)

    async def send_alert(self, message: object) -> None:
        """The instant alert as an embed card (colored by severity, linked title)."""
        from app.services.alert_message import render_discord as render_alert

        body = render_alert(message)
        body["allowed_mentions"] = discord_allowed_mentions(body.get("content", ""))
        await self._post(body)


class GenericWebhookChannel(_WebhookChannel):
    def _payload(self, text: str) -> dict:
        return {"text": text}
