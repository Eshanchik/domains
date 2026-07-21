"""SSRF guard for server-side fetches of user-supplied URLs (SEC / T39).

Health-check URLs are entered by operators and fetched by the worker. Without a
guard a URL like ``http://169.254.169.254/…`` (cloud metadata), ``http://localhost``
or an internal service could be reached from inside the network. ``validate_public_url``
rejects non-HTTP(S) schemes and any host that resolves to a private/reserved address;
it is called right before each request (and on every redirect hop). The resolver is a
module-level seam so tests don't depend on real DNS.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeUrlError(Exception):
    """The URL is not allowed for a server-side fetch (scheme or blocked address)."""


def _resolve(host: str) -> list[str]:
    """Return the IP addresses ``host`` resolves to (seam for tests)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _is_blocked_ip(value: str) -> bool:
    addr = ipaddress.ip_address(value)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_scheme(url: str) -> None:
    """Cheap check (no DNS): allow only http/https and block literal private IPs.

    Used at creation time for fast operator feedback. The authoritative check is
    ``validate_public_url`` at fetch time.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme not allowed: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("missing host")
    try:
        if _is_blocked_ip(host):
            raise UnsafeUrlError(f"blocked address: {host}")
    except ValueError:
        pass  # not a literal IP — DNS check happens at fetch time


def validate_public_url(url: str) -> None:
    """Full check: scheme, and every resolved address must be public.

    Raises ``UnsafeUrlError`` for a bad scheme or when the host resolves to a
    private/reserved address. If the host cannot be resolved we do not raise (the
    request will simply fail); we only block what we can prove is unsafe.
    """
    validate_scheme(url)
    host = urlparse(url).hostname
    assert host is not None  # validate_scheme guarantees it
    try:
        ipaddress.ip_address(host)
        return  # literal IP already vetted by validate_scheme
    except ValueError:
        pass
    try:
        addresses = _resolve(host)
    except socket.gaierror:
        return  # unresolvable — the fetch will fail on its own
    for ip in addresses:
        if _is_blocked_ip(ip):
            raise UnsafeUrlError(f"host {host} resolves to blocked address {ip}")
