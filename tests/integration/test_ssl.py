"""SSL check: valid / expired / self-signed / unreachable (DB + Redis, mocked TLS)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.checks import ssl_check
from app.checks.ssl_check import run_ssl_check
from app.db import SessionLocal, get_redis
from app.models.check_result import CheckResult
from app.models.ssl_certificate import SslCertificate
from tests.unit.test_ssl_parse import make_cert_der


def _run(coro):
    return asyncio.run(coro)


def _mock_fetch(monkeypatch, der, verify_error, reachable):
    monkeypatch.setattr(
        ssl_check, "_fetch_der", lambda host, port=443, timeout=10.0: (der, verify_error, reachable)
    )


def _check(dom):
    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                status = await run_ssl_check(s, redis, dom)
            async with SessionLocal() as s:
                certs = (
                    (await s.execute(select(SslCertificate).where(SslCertificate.domain_id == dom)))
                    .scalars()
                    .all()
                )
                cr = (
                    (await s.execute(select(CheckResult).where(CheckResult.type == "ssl")))
                    .scalars()
                    .all()
                )
                return status, len(list(certs)), len(list(cr))
        finally:
            await redis.aclose()

    return _run(run())


def test_valid_cert_is_ok(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    der = make_cert_der(
        "example.com",
        datetime.now(UTC) - timedelta(days=10),
        datetime.now(UTC) + timedelta(days=60),
        san=["example.com", "www.example.com"],
    )
    _mock_fetch(monkeypatch, der, None, True)

    status, n_certs, n_cr = _check(dom)
    assert status == "ok"
    assert n_certs == 2  # apex + www
    assert n_cr == 1


def test_expired_cert_is_fail(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    der = make_cert_der(
        "example.com",
        datetime.now(UTC) - timedelta(days=400),
        datetime.now(UTC) - timedelta(days=10),  # already expired
    )
    _mock_fetch(monkeypatch, der, None, True)
    status, _certs, _cr = _check(dom)
    assert status == "fail"


def test_self_signed_verify_error_is_warn(make_company, make_project, make_domain, monkeypatch):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    der = make_cert_der(
        "example.com",
        datetime.now(UTC) - timedelta(days=1),
        datetime.now(UTC) + timedelta(days=60),
    )
    _mock_fetch(monkeypatch, der, "verify: self signed certificate", True)
    status, _certs, _cr = _check(dom)
    assert status == "warn"


def test_unreachable_host_is_warn_and_recorded(
    make_company, make_project, make_domain, monkeypatch
):
    acme = make_company(code="acme")
    proj = make_project(acme, code="web")
    dom = make_domain(proj, fqdn="example.com")
    _mock_fetch(monkeypatch, None, "connection refused", False)

    async def run():
        redis = get_redis()
        try:
            async with SessionLocal() as s:
                status = await run_ssl_check(s, redis, dom)
            async with SessionLocal() as s:
                errs = (
                    await s.execute(
                        select(func.count())
                        .select_from(SslCertificate)
                        .where(SslCertificate.error.is_not(None))
                    )
                ).scalar_one()
                return status, errs
        finally:
            await redis.aclose()

    status, errs = _run(run())
    assert status == "warn"
    assert errs == 2  # both hosts recorded with an error
