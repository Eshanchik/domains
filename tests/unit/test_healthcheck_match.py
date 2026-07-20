"""Health-check status/pattern matching."""

from __future__ import annotations

from app.checks.healthcheck import pattern_matches, status_matches


def test_status_matches_list() -> None:
    assert status_matches(301, "301,302") is True
    assert status_matches(302, "301,302") is True
    assert status_matches(200, "301,302") is False


def test_status_matches_range() -> None:
    assert status_matches(204, "200-299") is True
    assert status_matches(301, "200-299") is False


def test_pattern_matches_substring_and_regex() -> None:
    assert pattern_matches("https://offer.example/landing", "offer.example") is True
    assert pattern_matches("https://offer.example/x", r"offer\.example/\w+") is True
    assert pattern_matches("https://other.example/", "offer.example") is False
    assert pattern_matches(None, "x") is False
