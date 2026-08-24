"""Slack / Discord / generic webhook channels."""

from __future__ import annotations

import json

import pytest
import respx

from app.channels.base import ChannelError, ChannelTransientError, chunk_message
from app.channels.webhook import DiscordChannel, GenericWebhookChannel, SlackChannel

URL = "https://hooks.example/xyz"


def test_chunk_message_within_limit_single() -> None:
    assert chunk_message("short", 2000) == ["short"]
    assert chunk_message("anything", None) == ["anything"]


def test_chunk_message_splits_on_line_boundaries() -> None:
    text = "\n".join(f"line-{i}" for i in range(100))  # ~700 chars
    chunks = chunk_message(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert len(chunks) > 1
    # No content lost and lines are not broken mid-way.
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_chunk_message_hard_splits_overlong_line() -> None:
    chunks = chunk_message("x" * 250, 100)
    assert [len(c) for c in chunks] == [100, 100, 50]


def test_payload_shapes() -> None:
    assert SlackChannel(URL)._payload("x") == {"text": "x"}
    assert DiscordChannel(URL)._payload("x") == {"content": "x"}
    assert GenericWebhookChannel(URL)._payload("x") == {"text": "x"}


@respx.mock
async def test_slack_success_sends_text() -> None:
    route = respx.post(URL).respond(200)
    await SlackChannel(URL).send("hello")
    assert json.loads(route.calls.last.request.content) == {"text": "hello"}


@respx.mock
async def test_discord_204_is_success() -> None:
    respx.post(URL).respond(204)
    await DiscordChannel(URL).send("hello")  # no exception → success


@respx.mock
@pytest.mark.parametrize("code", [429, 500, 503])
async def test_transient_statuses_retry(code: int) -> None:
    respx.post(URL).respond(code)
    with pytest.raises(ChannelTransientError):
        await GenericWebhookChannel(URL).send("hi")


@respx.mock
async def test_4xx_is_permanent() -> None:
    respx.post(URL).respond(400)
    with pytest.raises(ChannelError):
        await SlackChannel(URL).send("hi")


@respx.mock
async def test_discord_long_message_is_chunked_under_2000() -> None:
    # A digest longer than Discord's 2000-char content limit must be split into
    # several messages (this is the GT1 bug: one 400 "Must be 2000 or fewer").
    route = respx.post(URL).respond(204)
    long_text = "\n".join(f"line number {i} with some padding text" for i in range(200))
    assert len(long_text) > 2000
    await DiscordChannel(URL).send(long_text)
    assert route.call_count > 1
    for call in route.calls:
        body = json.loads(call.request.content)
        assert len(body["content"]) <= 2000


@respx.mock
async def test_discord_send_digest_posts_embeds() -> None:
    from app.services.digest import Digest, DigestGroup, DigestRow, DigestTier

    route = respx.post(URL).respond(204)
    digest = Digest(
        scope_name="Adera",
        dashboard_url="https://dg.example",
        generated_label="18.08.2026 09:00 · Kyiv",
        total=1,
        tiers=[
            DigestTier(
                "crit",
                0xE5484D,
                "🔴",
                "Критично",
                [
                    DigestGroup(
                        1,
                        "🔴",
                        "Истекают ≤7 дней",
                        [
                            DigestRow(
                                fqdn="a.com",
                                kind="expiry",
                                days=2,
                                url="https://dg.example/domains/1",
                            )
                        ],
                    )
                ],
            )
        ],
    )
    await DiscordChannel(URL).send_digest(digest)
    body = json.loads(route.calls.last.request.content)
    assert body["embeds"][0]["title"] == "Ежедневная сводка"  # lead embed
    assert "a.com" in json.dumps(body, ensure_ascii=False)  # domain rendered
