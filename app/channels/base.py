"""Notification channel plugin interface (SPEC FR-AL-1)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ChannelError(Exception):
    """Permanent delivery failure (bad config, forbidden, etc.)."""


class ChannelTransientError(Exception):
    """Temporary delivery failure (rate limit, 5xx) — retry."""


def chunk_message(text: str, limit: int | None) -> list[str]:
    """Split ``text`` into pieces no longer than ``limit`` characters.

    Breaks on line boundaries where possible so a digest stays readable; a single line
    longer than the limit is hard-split. ``limit is None`` (or text within the limit)
    returns the text unchanged as a single chunk. Used to respect per-channel message
    caps (e.g. Discord's 2000-char ``content`` limit, Telegram's 4096).
    """
    if limit is None or limit <= 0 or len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # A single line longer than the limit cannot fit any chunk — hard-split it.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        addition = line if not current else "\n" + line
        if current and len(current) + len(addition) > limit:
            chunks.append(current)
            current = line
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


class NotificationChannel(ABC):
    """A destination able to deliver a text message.

    Concrete channels implement :meth:`_send_one` (deliver a single message). The base
    :meth:`send` splits over-long text into ``MAX_LEN``-sized chunks first, so callers
    never have to worry about a channel's message-size cap.
    """

    # Maximum characters a single message may carry; ``None`` means no limit.
    MAX_LEN: int | None = None

    async def send(self, text: str) -> None:
        """Deliver ``text``, splitting it to fit ``MAX_LEN``. Raise ChannelTransientError
        to trigger a retry (of the whole send)."""
        for chunk in chunk_message(text, self.MAX_LEN):
            await self._send_one(chunk)

    @abstractmethod
    async def _send_one(self, text: str) -> None:
        """Deliver a single already-size-bounded message."""
