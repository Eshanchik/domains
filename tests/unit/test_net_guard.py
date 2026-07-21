"""SSRF guard: scheme allowlist + private/reserved address blocking."""

from __future__ import annotations

import pytest

from app.core import net_guard
from app.core.net_guard import UnsafeUrlError


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://example.com",
        "javascript:alert(1)",
        "//no-scheme",
    ],
)
def test_validate_scheme_rejects_non_http(url):
    with pytest.raises(UnsafeUrlError):
        net_guard.validate_scheme(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/internal",
        "http://[::1]/x",
        "http://0.0.0.0/x",
    ],
)
def test_validate_scheme_blocks_literal_private_ips(url):
    with pytest.raises(UnsafeUrlError):
        net_guard.validate_scheme(url)


def test_validate_scheme_allows_public_literal_and_hostnames():
    net_guard.validate_scheme("https://example.com/health")
    net_guard.validate_scheme("http://93.184.216.34/x")  # public literal IP


def test_validate_public_url_blocks_hostname_resolving_private(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolve", lambda host: ["10.1.2.3"])
    with pytest.raises(UnsafeUrlError):
        net_guard.validate_public_url("http://sneaky.internal/x")


def test_validate_public_url_blocks_when_any_ip_private(monkeypatch):
    # A host that resolves to both a public and a private IP is still blocked.
    monkeypatch.setattr(net_guard, "_resolve", lambda host: ["93.184.216.34", "192.168.1.9"])
    with pytest.raises(UnsafeUrlError):
        net_guard.validate_public_url("http://mixed.example/x")


def test_validate_public_url_allows_public(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolve", lambda host: ["93.184.216.34"])
    net_guard.validate_public_url("https://example.com/health")


def test_validate_public_url_unresolvable_does_not_raise(monkeypatch):
    import socket

    def _boom(host):
        raise socket.gaierror("nope")

    monkeypatch.setattr(net_guard, "_resolve", _boom)
    # Cannot prove unsafe → no raise (the request will fail on its own).
    net_guard.validate_public_url("https://does-not-resolve.invalid/x")
