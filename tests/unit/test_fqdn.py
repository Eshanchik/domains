"""FQDN normalization and IDN handling."""

from __future__ import annotations

import pytest

from app.core.fqdn import InvalidDomainError, normalize_fqdn


def test_lowercase_and_strip_scheme_path_port() -> None:
    n = normalize_fqdn("HTTPS://Example.COM:443/path?x=1")
    assert n.fqdn == "example.com"
    assert n.punycode == "example.com"
    assert n.tld == "com"


def test_trailing_dot_removed() -> None:
    assert normalize_fqdn("example.com.").fqdn == "example.com"


def test_idn_unicode_to_punycode() -> None:
    n = normalize_fqdn("münchen.de")
    assert n.punycode == "xn--mnchen-3ya.de"
    assert n.fqdn == "münchen.de"
    assert n.tld == "de"


def test_idn_punycode_input_normalizes_to_same_unicode() -> None:
    # Entering the punycode form must dedupe with the Unicode form (same canonical fqdn).
    unicode_form = normalize_fqdn("münchen.de")
    puny_form = normalize_fqdn("xn--mnchen-3ya.de")
    assert puny_form.fqdn == unicode_form.fqdn
    assert puny_form.punycode == unicode_form.punycode


@pytest.mark.parametrize("bad", ["", "   ", "nodot", "http://", "..", "-"])
def test_invalid_domains_raise(bad: str) -> None:
    with pytest.raises(InvalidDomainError):
        normalize_fqdn(bad)
