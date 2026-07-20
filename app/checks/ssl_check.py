"""SSL/TLS certificate check (SPEC FR-CK-2).

Checks apex + www + any extra hosts on a domain. For each host we fetch the leaf
certificate (even if invalid, so we can still report dates) and separately attempt a
verifying handshake to detect chain/handshake errors. ``_fetch_der`` is the network
seam tests patch.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime

import redis.asyncio as aioredis
from cryptography import x509
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.check_result_store import write_result
from app.core import rate_limiter
from app.models.check_result import CheckStatus
from app.models.domain import Domain
from app.models.ssl_certificate import SslCertificate

log = logging.getLogger("checks.ssl")

SSL_CAPACITY = 10.0
SSL_REFILL = 10.0


@dataclass
class HostResult:
    host: str
    reachable: bool = True
    issuer: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    san: list[str] = field(default_factory=list)
    error: str | None = None
    status: str = CheckStatus.ok


def _fetch_der(
    host: str, port: int = 443, timeout: float = 10.0
) -> tuple[bytes | None, str | None, bool]:
    """Return (leaf_cert_der, verify_error, reachable). Network IO — patched in tests."""
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            unverified.wrap_socket(sock, server_hostname=host) as ssock,
        ):
            der = ssock.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as exc:
        return None, str(exc), False

    verify_error: str | None = None
    verifying = ssl.create_default_context()
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            verifying.wrap_socket(sock, server_hostname=host),
        ):
            pass
    except ssl.SSLCertVerificationError as exc:
        verify_error = f"verify: {exc.verify_message or exc}"
    except (ssl.SSLError, OSError) as exc:
        verify_error = f"handshake: {exc}"
    return der, verify_error, True


def parse_cert(der: bytes) -> tuple[str | None, datetime | None, datetime | None, list[str]]:
    cert = x509.load_der_x509_certificate(der)
    issuer = cert.issuer.rfc4514_string()
    valid_from = cert.not_valid_before_utc
    valid_to = cert.not_valid_after_utc
    san: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san = ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        pass
    return issuer, valid_from, valid_to, san


async def check_host(
    host: str, *, now: datetime | None = None, timeout: float = 10.0
) -> HostResult:
    ts = now or datetime.now(UTC)
    der, verify_error, reachable = await asyncio.to_thread(_fetch_der, host, 443, timeout)
    if not reachable or der is None:
        return HostResult(host=host, reachable=False, error=verify_error, status=CheckStatus.warn)

    issuer, valid_from, valid_to, san = parse_cert(der)
    if valid_to is not None and valid_to < ts:
        status = CheckStatus.fail  # expired
    elif verify_error:
        status = CheckStatus.warn  # chain/handshake problem
    else:
        status = CheckStatus.ok
    return HostResult(
        host=host,
        issuer=issuer,
        valid_from=valid_from,
        valid_to=valid_to,
        san=san,
        error=verify_error,
        status=status,
    )


def hosts_for(domain: Domain) -> list[str]:
    """apex + www + extra hosts, de-duplicated, using the ASCII/punycode form."""
    apex = domain.punycode
    ordered = [apex, f"www.{apex}", *domain.ssl_extra_hosts]
    seen: set[str] = set()
    result: list[str] = []
    for host in ordered:
        host = host.strip().lower()
        if host and host not in seen:
            seen.add(host)
            result.append(host)
    return result


_SEVERITY = {CheckStatus.ok: 0, CheckStatus.warn: 1, CheckStatus.fail: 2}


async def run_ssl_check(
    session: AsyncSession,
    redis: aioredis.Redis,
    domain_id: int,
    *,
    now: datetime | None = None,
) -> str:
    """Check SSL for all of a domain's hosts; persist certs + a summary CheckResult."""
    ts = now or datetime.now(UTC)
    domain = await session.get(Domain, domain_id)
    if domain is None:
        return "missing"

    allowed, _retry = await rate_limiter.acquire_token(
        redis, rate_limiter.service_key("ssl"), capacity=SSL_CAPACITY, refill_rate=SSL_REFILL
    )
    if not allowed:
        return "rate_limited"

    results = [await check_host(host, now=ts) for host in hosts_for(domain)]

    for r in results:
        session.add(
            SslCertificate(
                domain_id=domain_id,
                host=r.host,
                issuer=r.issuer,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
                san=r.san,
                error=r.error,
                checked_at=ts,
            )
        )

    overall = max(results, key=lambda r: _SEVERITY[r.status]).status if results else CheckStatus.ok
    await write_result(
        session,
        domain_id=domain_id,
        check_type="ssl",
        status=overall,
        data={
            "hosts": [
                {
                    "host": r.host,
                    "status": r.status,
                    "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                    "error": r.error,
                }
                for r in results
            ]
        },
        checked_at=ts,
    )
    await session.commit()
    return overall
