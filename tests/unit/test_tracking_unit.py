"""T99: tracking helpers — error wording, durations, status-page access matching."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.services import tracking as t
from app.services.tracking import AccessRule, TrackingError


def test_humanize_error_is_understandable() -> None:
    assert t.humanize_error("request failed: ", None).startswith("нет соединения")
    assert t.humanize_error("request failed: ConnectError", None).startswith("нет соединения")
    assert t.humanize_error("request failed: ReadTimeout", None).startswith("таймаут")
    assert t.humanize_error("unexpected status 404", 404) == "неожиданный ответ HTTP 404"
    assert t.humanize_error("location pattern mismatch", 302).startswith("редирект ведёт не туда")
    assert t.humanize_error(None, 500) == "HTTP 500"
    assert t.humanize_error("", None) == ""


def test_duration_label() -> None:
    assert t.duration_label(timedelta(days=10, hours=4, minutes=5)) == "10 д 4 ч"
    assert t.duration_label(timedelta(hours=3, minutes=7)) == "3 ч 7 мин"
    assert t.duration_label(timedelta(seconds=20)) == "1 мин"


RULES = [AccessRule("adera.agency", 3)]


def test_access_requires_email_domain_and_workspace_hd() -> None:
    assert t.match_access(RULES, "Ivan@Adera.Agency", "adera.agency") == RULES[0]
    # A personal Google account on the work address has no `hd` → refused.
    assert t.match_access(RULES, "ivan@adera.agency", None) is None
    # hd of another Workspace, or a look-alike domain → refused.
    assert t.match_access(RULES, "ivan@adera.agency", "evil.com") is None
    assert t.match_access(RULES, "ivan@evil-adera.agency", "evil-adera.agency") is None
    assert t.match_access(RULES, "ivan@sub.adera.agency", "sub.adera.agency") is None
    assert t.match_access(RULES, "not-an-email", "adera.agency") is None
    assert t.match_access([], "ivan@adera.agency", "adera.agency") is None


def test_normalize_email_domain() -> None:
    assert t.normalize_email_domain(" @Adera.Agency ") == "adera.agency"
    for bad in ("", "adera", "a b.com", "x@y.com", "https://adera.agency"):
        with pytest.raises(TrackingError):
            t.normalize_email_domain(bad)
