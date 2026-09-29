"""T101: the instant alert as a rich message — Discord embed, Telegram HTML, plain."""

from __future__ import annotations

import json

import respx

from app.channels.telegram import TelegramChannel
from app.channels.webhook import DiscordChannel, SlackChannel
from app.services.alert_message import (
    AlertMessage,
    _days_phrase,
    render_discord,
    render_plain,
    render_telegram,
)

URL = "https://hooks.example/xyz"


def _msg(**kw) -> AlertMessage:
    base = {
        "event_id": 945,
        "kind": "expiry",
        "severity": "high",
        "color": 0xE5484D,
        "emoji": "🔴",
        "label": "Просрочен домен",
        "fqdn": "rca.lol",
        "url": "https://dg.example/alerts/945",
        "summary": "просрочен 43 дн. — истёк 17.08.2026",
        "location": "Adera",
        "facts": [
            ("Автопродление", "✖ выключено"),
            ("Регистратор", "Namecheap"),
            ("Аккаунт", "Adera"),
        ],
        "hint": "Домен уже истёк. Если он нужен — срочно продлите у регистратора Namecheap.",
        "mention": "",
        "footer": "Алерт #945 · Критично",
        "fired_iso": "2026-09-29T09:02:26+00:00",
    }
    base.update(kw)
    return AlertMessage(**base)


def test_days_phrase_never_says_minus() -> None:
    assert _days_phrase(-43, "17.08.2026") == "просрочен 43 дн. — истёк 17.08.2026"
    assert _days_phrase(0, "30.09.2026") == "истекает сегодня — 30.09.2026"
    assert _days_phrase(5, "05.10.2026") == "истекает через 5 дн. — 05.10.2026"


def test_discord_card_structure() -> None:
    body = render_discord(_msg())
    [e] = body["embeds"]
    assert e["title"] == "Просрочен домен: rca.lol" and e["url"].endswith("/alerts/945")
    assert e["color"] == 0xE5484D
    assert e["description"] == "**просрочен 43 дн. — истёк 17.08.2026**"
    assert e["author"]["name"] == "DomainGuard · Adera"
    names = [f["name"] for f in e["fields"]]
    assert names == ["Автопродление", "Регистратор", "Аккаунт", "▸ Что делать"]
    assert e["fields"][-1]["inline"] is False and e["footer"]["text"] == "Алерт #945 · Критично"
    assert e["timestamp"] == "2026-09-29T09:02:26+00:00"
    assert body["content"] == "🔴 **Просрочен домен** · rca.lol"  # push-notification headline


def test_discord_card_pings_owner_in_content_and_respects_limits() -> None:
    long = "x" * 3000
    body = render_discord(
        _msg(mention="<@123456789012345678>", hint=long, facts=[("Длинное", long)] * 40)
    )
    assert body["content"].endswith("\n👤 <@123456789012345678>")
    [e] = body["embeds"]
    assert len(e["fields"]) <= 25
    assert all(len(f["value"]) <= 1024 for f in e["fields"])


@respx.mock
async def test_discord_channel_posts_the_embed_with_safe_mentions() -> None:
    route = respx.post(URL).respond(204)
    await DiscordChannel(URL).send_alert(_msg(mention="<@123456789012345678>"))
    body = json.loads(route.calls.last.request.content)
    assert body["embeds"][0]["title"] == "Просрочен домен: rca.lol"
    assert body["allowed_mentions"] == {
        "parse": [],
        "users": ["123456789012345678"],
        "roles": [],
    }


@respx.mock
async def test_telegram_channel_sends_html() -> None:
    route = respx.post("https://api.telegram.org/botT/sendMessage").respond(200)
    await TelegramChannel("T", "-100").send_alert(_msg(fqdn="a&b.com", mention="@vasya_ops"))
    body = json.loads(route.calls.last.request.content)
    assert body["parse_mode"] == "HTML"
    assert '<a href="https://dg.example/alerts/945">a&amp;b.com</a>' in body["text"]
    assert "<b>просрочен 43 дн. — истёк 17.08.2026</b>" in body["text"]
    assert "👤 @vasya_ops" in body["text"]


def test_telegram_escapes_data() -> None:
    html = render_telegram(_msg(facts=[("Регистратор", "<script>")]))
    assert "&lt;script&gt;" in html and "<script>" not in html


@respx.mock
async def test_slack_alert_is_escaped_plain_text() -> None:
    route = respx.post(URL).respond(200)
    await SlackChannel(URL).send_alert(_msg(hint="<!channel> срочно"))
    text = json.loads(route.calls.last.request.content)["text"]
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text
    assert text.startswith("🔴 Просрочен домен · rca.lol")


def test_plain_has_everything() -> None:
    text = render_plain(_msg())
    for part in (
        "просрочен 43 дн.",
        "📁 Adera",
        "• Автопродление: ✖ выключено",
        "▸ Что делать",
        "🔗 ",
    ):
        assert part in text
