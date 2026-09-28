"""T97: people validation, @-mention rendering per channel, Discord allowed_mentions."""

from __future__ import annotations

import json

import pytest
import respx

from app.channels.webhook import DiscordChannel, discord_allowed_mentions
from app.services import people
from app.services.people import PersonError, PersonRef

URL = "https://hooks.example/xyz"

VASYA = PersonRef(1, "person", "Вася", "vasya", "123456789012345678", "vasya_ops")
OPS = PersonRef(2, "group", "Ops", "ops", "987654321098765432", None)
NOIDS = PersonRef(3, "person", "Петя", "petya", None, None)
BY_HANDLE = {"vasya": VASYA, "ops": OPS, "petya": NOIDS}


# --- validation ------------------------------------------------------------------


def test_normalize_handle() -> None:
    assert people.normalize_handle(" @Vasya ") == "vasya"
    assert people.normalize_handle("ops.team_1") == "ops.team_1"
    for bad in ("", "вася", "has space", "-lead"):
        with pytest.raises(PersonError):
            people.normalize_handle(bad)


def test_normalize_discord_id_accepts_raw_and_pasted_mentions() -> None:
    assert people.normalize_discord_id("123456789012345678") == "123456789012345678"
    assert people.normalize_discord_id("<@123456789012345678>") == "123456789012345678"
    assert people.normalize_discord_id("<@!123456789012345678>") == "123456789012345678"
    assert people.normalize_discord_id("<@&987654321098765432>") == "987654321098765432"
    assert people.normalize_discord_id("  ") is None
    for bad in ("vasya#1234", "12345", "12345678901234567890123"):
        with pytest.raises(PersonError):
            people.normalize_discord_id(bad)


def test_normalize_telegram() -> None:
    assert people.normalize_telegram("@vasya_ops") == "vasya_ops"
    assert people.normalize_telegram("https://t.me/vasya_ops") == "vasya_ops"
    assert people.normalize_telegram("") is None
    for bad in ("ab", "вася", "1abcde", "has space"):
        with pytest.raises(PersonError):
            people.normalize_telegram(bad)


# --- mentions ----------------------------------------------------------------------


def test_mention_per_channel_type() -> None:
    assert people.mention(VASYA, "discord") == "<@123456789012345678>"
    assert people.mention(OPS, "discord") == "<@&987654321098765432>"
    assert people.mention(VASYA, "telegram") == "@vasya_ops"
    # No id for the network → the name, never a broken/fake ping.
    assert people.mention(OPS, "telegram") == "Ops"
    assert people.mention(NOIDS, "discord") == "Петя"
    assert people.mention(VASYA, "slack") == "Вася (@vasya)"
    assert people.mention(None, "discord") == ""


def test_render_mentions_replaces_known_handles_only() -> None:
    text = "@vasya оплати, @ops глянь NS, @unknown и mail@vasya.com не трогаем"
    out = people.render_mentions(text, BY_HANDLE, "discord")
    assert "<@123456789012345678> оплати" in out
    assert "<@&987654321098765432> глянь" in out
    assert "@unknown" in out
    assert "mail@vasya.com" in out  # an e-mail is not a mention


def test_extract_mentions_dedup_and_case() -> None:
    found = people.extract_mentions("@Vasya @vasya @OPS @nobody", BY_HANDLE)
    assert [p.handle for p in found] == ["vasya", "ops"]


# --- Discord ping safety -----------------------------------------------------------


def test_allowed_mentions_lists_only_explicit_ids() -> None:
    content = "@everyone <@123456789012345678> <@!123456789012345678> <@&987654321098765432>"
    am = discord_allowed_mentions(content)
    assert am == {
        "parse": [],  # @everyone / @here are never parsed
        "users": ["123456789012345678"],
        "roles": ["987654321098765432"],
    }


@respx.mock
async def test_discord_send_pings_only_mentioned_user() -> None:
    route = respx.post(URL).respond(204)
    await DiscordChannel(URL).send("🔴 alert\n👤 <@123456789012345678>\n@here")
    body = json.loads(route.calls.last.request.content)
    assert body["allowed_mentions"] == {
        "parse": [],
        "users": ["123456789012345678"],
        "roles": [],
    }


# --- digest owners line --------------------------------------------------------------


def _digest_with_owners():
    from app.services.digest import Digest, DigestGroup, DigestRow, DigestTier

    row = DigestRow(fqdn="a.com", kind="expiry", days=3, assignee=VASYA)
    return Digest(
        scope_name="Adera",
        dashboard_url=None,
        generated_label="now",
        total=1,
        tiers=[DigestTier("crit", 0xE5484D, "🔴", "Критично", [DigestGroup(1, "🔴", "≤7", [row])])],
        owners=[(VASYA, 1), (OPS, 4)],
    )


def test_owners_line_dialects() -> None:
    from app.services.digest import owners_line, render_plain, render_telegram_html

    d = _digest_with_owners()
    assert owners_line(d, "discord") == (
        "👤 Ответственные: <@123456789012345678> ×1 · <@&987654321098765432> ×4"
    )
    assert "@vasya_ops ×1" in render_telegram_html(d)
    plain = render_plain(d)
    assert "Вася (@vasya) ×1" in plain
    assert "👤 Вася" in plain  # the row shows its owner by name


@respx.mock
async def test_discord_digest_pings_owners_in_content() -> None:
    route = respx.post(URL).respond(204)
    await DiscordChannel(URL).send_digest(_digest_with_owners())
    first = json.loads(route.calls[0].request.content)
    assert first["content"].startswith("👤 Ответственные:")
    assert first["allowed_mentions"]["users"] == ["123456789012345678"]
    assert first["allowed_mentions"]["roles"] == ["987654321098765432"]
    assert first["embeds"]  # the rich cards are still there


# --- review fixes -------------------------------------------------------------------


def test_mention_before_punctuation_resolves() -> None:
    out = people.render_mentions("Передаю @vasya. Потом @ops-", BY_HANDLE, "discord")
    assert out == "Передаю <@123456789012345678>. Потом <@&987654321098765432>-"
    for bad in ("vasya.", "ops-"):
        with pytest.raises(PersonError):
            people.normalize_handle(bad)
    assert people.normalize_handle("a") == "a"  # single-char handles still valid


def _event_and_domain():
    from types import SimpleNamespace

    event = SimpleNamespace(id=7, severity="high", kind="expiry")
    domain = SimpleNamespace(fqdn="a.com")
    return event, domain


def test_slack_comment_cannot_ping_the_channel() -> None:
    from app.services.alert_workflow import comment_message

    event, domain = _event_and_domain()
    text = comment_message(
        event,
        domain,
        "<!channel> срочно @vasya",
        actor_name="mgr",
        by_handle=BY_HANDLE,
        channel_type="slack",
    )
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text
    assert "Вася (@vasya)" in text


def test_raw_discord_ids_in_a_comment_do_not_ping() -> None:
    from app.services.alert_workflow import comment_message

    event, domain = _event_and_domain()
    text = comment_message(
        event,
        domain,
        "<@&111111111111111111> и <@222222222222222222>, @vasya глянь",
        actor_name="mgr",
        by_handle=BY_HANDLE,
        channel_type="discord",
    )
    am = discord_allowed_mentions(text)
    # Only the directory person rendered by us is pinged; raw ids typed by a user are not.
    assert am["users"] == ["123456789012345678"] and am["roles"] == []
