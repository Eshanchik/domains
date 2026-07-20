"""Import parsing (bulk + CSV)."""

from __future__ import annotations

from app.services.import_domains import parse_bulk, parse_csv


def test_parse_bulk_skips_blanks_and_comments() -> None:
    rows = parse_bulk("example.com\n\n  \n# comment\nanother.org\n")
    assert [r.raw_fqdn for r in rows] == ["example.com", "another.org"]
    assert rows[0].line == 1
    assert rows[1].line == 5


def test_parse_csv_reads_columns() -> None:
    text = (
        "fqdn,project_code,tags,notes,renewal_price,currency\n"
        'example.com,web,"a,b",note,12.50,EUR\n'
    )
    rows = parse_csv(text)
    assert len(rows) == 1
    r = rows[0]
    assert r.raw_fqdn == "example.com"
    assert r.project_code == "web"
    assert r.tags == ["a", "b"]
    assert r.renewal_price == "12.50"
    assert r.currency == "EUR"


def test_parse_csv_missing_fqdn_marks_empty() -> None:
    rows = parse_csv("fqdn,notes\n,orphan\n")
    assert rows[0].raw_fqdn == ""
