"""Notification channel plugin interface (SPEC FR-AL-1)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ChannelError(Exception):
    """Permanent delivery failure (bad config, forbidden, etc.)."""


class ChannelTransientError(Exception):
    """Temporary delivery failure (rate limit, 5xx) — retry."""


class NotificationChannel(ABC):
    """A destination able to deliver a text message."""

    @abstractmethod
    async def send(self, text: str) -> None:
        """Deliver ``text``. Raise ChannelTransientError to trigger a retry."""
