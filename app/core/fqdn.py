"""FQDN normalization and IDN handling (SPEC FR-DM-1).

Produces a canonical, deduplicable representation of a domain name:
- ``fqdn``: lowercased Unicode form (canonical key, UNIQUE in the DB)
- ``punycode``: ASCII/Punycode form (``xn--...``)
- ``tld``: last label of the punycode form

Both the Unicode form ("münchen.de") and its Punycode form ("xn--mnchen-3ya.de")
normalize to the same ``fqdn`` so re-imports dedupe (FR-DM-3).
"""

from __future__ import annotations

from dataclasses import dataclass

import idna


@dataclass(frozen=True)
class NormalizedDomain:
    fqdn: str
    punycode: str
    tld: str


class InvalidDomainError(ValueError):
    """Raised when a string cannot be parsed as a domain name."""


def normalize_fqdn(raw: str) -> NormalizedDomain:
    """Normalize arbitrary user input into a canonical domain representation.

    Strips scheme/path/port and a trailing dot, applies IDNA/UTS-46, and returns
    the Unicode + Punycode + TLD triple. Raises ``InvalidDomainError`` on garbage.
    """
    if raw is None:
        raise InvalidDomainError("empty domain")

    value = raw.strip().lower()
    # Drop scheme and any path/query if a full URL was pasted.
    if "//" in value:
        value = value.split("//", 1)[1]
    value = value.split("/", 1)[0].split("?", 1)[0]
    # Drop a port if present, and a trailing dot.
    value = value.split(":", 1)[0].rstrip(".")

    if not value or "." not in value:
        raise InvalidDomainError(f"not a domain: {raw!r}")

    try:
        # uts46=True lowercases and normalizes; transitional=False for modern IDNA2008.
        punycode = idna.encode(value, uts46=True, transitional=False).decode("ascii")
        fqdn = idna.decode(punycode)
    except idna.IDNAError as exc:
        raise InvalidDomainError(f"invalid domain {raw!r}: {exc}") from exc

    tld = punycode.rsplit(".", 1)[-1]
    return NormalizedDomain(fqdn=fqdn, punycode=punycode, tld=tld)
