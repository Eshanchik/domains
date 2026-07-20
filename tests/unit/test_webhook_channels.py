"""Slack / Discord / generic webhook channels."""

from __future__ import annotations

import json

import pytest
import respx

from app.channels.base import ChannelError, ChannelTransientError
from app.channels.webhook import DiscordChannel, GenericWebhookChannel, SlackChannel

URL = "https://hooks.example/xyz"


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
