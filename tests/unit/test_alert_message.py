"""Clear multi-line alert message formatting (T50)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.alert import AlertEvent
from app.models.domain import Domain
from app.services.alerts import build_message


def _domain(fqdn: str = "ex.com", expiry=datetime(2027, 1, 15, tzinfo=UTC)) -> Domain:
    return Domain(fqdn=fqdn, punycode=fqdn, tld="com", project_id=1, expiry_date=expiry)


def _event(kind: str, severity: str = "high", payload: dict | None = None) -> AlertEvent:
    return AlertEvent(
        domain_id=1,
        kind=kind,
        dedupe_key="k",
        severity=severity,
        state="active",
        payload_json=payload or {},
    )


def test_expiry_message_has_severity_date_threshold_location():
    msg = build_message(
        _event("expiry", payload={"days": 12, "threshold": 30}),
        _domain(),
        project="Web",
        company="ACME",
    )
    assert "HIGH" in msg
    assert "ex.com" in msg
    assert "2027-01-15" in msg  # exact expiry date
    assert "12 дн" in msg
    assert "≤30" in msg
    assert "Web · ACME" in msg  # project · company context
    assert "\n" in msg  # multi-line


def test_ssl_message():
    msg = build_message(
        _event("ssl", payload={"days": 5, "threshold": 14}), _domain(), project="P", company="C"
    )
    assert "SSL" in msg and "5 дн" in msg and "P · C" in msg


def test_vt_message():
    msg = build_message(_event("vt_malicious", payload={"malicious": 3}), _domain())
    assert "VirusTotal" in msg and "3" in msg


def test_ns_change_message():
    msg = build_message(
        _event("ns_change", severity="medium", payload={"old_ns": ["a"], "new_ns": ["b"]}),
        _domain(),
    )
    assert "NS" in msg and "было" in msg and "стало" in msg


def test_location_omitted_when_absent():
    msg = build_message(_event("expiry", payload={"days": 1, "threshold": 7}), _domain())
    assert "📁" not in msg  # no location line without project/company


def test_account_shown_in_message():
    msg = build_message(
        _event("expiry", payload={"days": 5, "threshold": 7}),
        _domain(),
        project="Web",
        company="ACME",
        account="Kingbilly",
    )
    assert "🏷 Kingbilly" in msg  # which registrar account to act on
    # Account also renders without project/company context.
    msg2 = build_message(_event("ssl", payload={"days": 3}), _domain(), account="Olympia")
    assert "🏷 Olympia" in msg2
