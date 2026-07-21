"""Compact alert-age formatting."""

from __future__ import annotations

from datetime import timedelta

from app.web.alerts import format_age


def test_format_age_days():
    assert format_age(timedelta(days=3, hours=4, minutes=30)) == "3д 4ч"


def test_format_age_hours():
    assert format_age(timedelta(hours=5, minutes=12)) == "5ч 12м"


def test_format_age_minutes():
    assert format_age(timedelta(minutes=8, seconds=59)) == "8м"


def test_format_age_clamped_non_negative():
    assert format_age(timedelta(seconds=-10)) == "0м"
