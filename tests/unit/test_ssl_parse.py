"""SSL cert parsing and host expansion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.checks.ssl_check import hosts_for, parse_cert
from app.models.domain import Domain


def make_cert_der(cn: str, not_before: datetime, not_after: datetime, san=None) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(h) for h in san]), critical=False
        )
    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.DER)


def test_parse_cert_extracts_dates_issuer_san() -> None:
    nb = datetime.now(UTC) - timedelta(days=1)
    na = datetime.now(UTC) + timedelta(days=90)
    der = make_cert_der("example.com", nb, na, san=["example.com", "www.example.com"])
    issuer, valid_from, valid_to, san = parse_cert(der)
    assert "example.com" in issuer
    assert valid_to > valid_from
    assert set(san) == {"example.com", "www.example.com"}


def test_hosts_for_dedups_apex_www_extra() -> None:
    d = Domain(
        project_id=1,
        fqdn="example.com",
        punycode="example.com",
        tld="com",
        ssl_extra_hosts=["api.example.com", "www.example.com"],
        field_sources={},
    )
    assert hosts_for(d) == ["example.com", "www.example.com", "api.example.com"]
