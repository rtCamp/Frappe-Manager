"""Advisory Cloudflare-proxy detection (`detect_cloudflare_proxy`).

Nothing here decides behaviour: the function this file pins is meant for one add-time nudge, and
every one of its failure paths -- no DNS record, a timeout, a missing `dig`, a malformed response,
an exception nobody anticipated -- must degrade to the same `undetermined` status rather than an
exception, because a stack trace over an advisory hint is a worse outcome than no hint at all.

`DNSValidator.validate_a_record` is mocked at the class method rather than at the `dig` subprocess
boundary: it is the sanctioned primitive this module composes, and pinning against its return shape
(`ValidationResult`) is what proves this module reads that contract correctly, independent of how
`DNSValidator` itself talks to `dig`.
"""

from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.modules.cdn_detection import (
    CDNProvider,
    CDNProxyStatus,
    detect_cloudflare_proxy,
)
from frappe_manager.ssl_manager.dns_validator import DNSValidator, ValidationResult


def _resolved(ip: str) -> ValidationResult:
    return ValidationResult(
        valid=True, actual_value=ip, expected_value=None, message=f"Domain resolves to {ip}", dns_query_output=""
    )


def _failed(message: str) -> ValidationResult:
    return ValidationResult(
        valid=False, actual_value=None, expected_value="An IP address", message=message, dns_query_output=""
    )


@pytest.mark.unit
class TestProxiedDetection:
    def test_an_address_inside_a_cloudflare_range_is_reported_proxied(self, mocker):
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("104.16.1.1"))

        result = detect_cloudflare_proxy("example.com")

        assert result.status == CDNProxyStatus.proxied
        assert result.provider == CDNProvider.cloudflare
        assert result.resolved_ip == "104.16.1.1"
        assert result.detail is None

    def test_an_address_outside_every_range_is_reported_not_proxied(self, mocker):
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("8.8.8.8"))

        result = detect_cloudflare_proxy("example.com")

        assert result.status == CDNProxyStatus.not_proxied
        assert result.provider is None
        assert result.resolved_ip == "8.8.8.8"


@pytest.mark.unit
class TestRangeBoundaries:
    """173.245.48.0/20 is vendored, isolated (no adjacent vendored range on either side), so its
    edges are unambiguous: 173.245.48.0-173.245.63.255 is Cloudflare, .47.255 and .64.0 are not."""

    def test_the_first_address_of_a_range_is_inside_it(self, mocker):
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("173.245.48.0"))
        assert detect_cloudflare_proxy("example.com").status == CDNProxyStatus.proxied

    def test_the_last_address_of_a_range_is_inside_it(self, mocker):
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("173.245.63.255"))
        assert detect_cloudflare_proxy("example.com").status == CDNProxyStatus.proxied

    def test_the_address_immediately_below_a_range_is_outside_it(self, mocker):
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("173.245.47.255"))
        assert detect_cloudflare_proxy("example.com").status == CDNProxyStatus.not_proxied

    def test_the_address_immediately_above_a_range_is_outside_it(self, mocker):
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("173.245.64.0"))
        assert detect_cloudflare_proxy("example.com").status == CDNProxyStatus.not_proxied


@pytest.mark.unit
class TestEveryFailureModeDegradesToUndetermined:
    def test_no_a_record_is_undetermined(self, mocker):
        mocker.patch.object(
            DNSValidator, "validate_a_record", return_value=_failed("No A record found for example.com")
        )

        result = detect_cloudflare_proxy("example.com")

        assert result.status == CDNProxyStatus.undetermined
        assert result.provider is None
        assert result.resolved_ip is None

    def test_a_dns_timeout_is_undetermined_never_not_proxied(self, mocker):
        """The defect a bool return type cannot avoid: a timeout is not evidence of a bare
        origin, and reporting it as `not_proxied` would be actively wrong, not just imprecise."""
        mocker.patch.object(
            DNSValidator, "validate_a_record", return_value=_failed("DNS query timeout after 3 seconds")
        )

        assert detect_cloudflare_proxy("example.com").status == CDNProxyStatus.undetermined

    def test_a_missing_dig_binary_is_undetermined(self, mocker):
        mocker.patch.object(
            DNSValidator,
            "validate_a_record",
            return_value=_failed("'dig' command not found. Install dnsutils/bind-tools package."),
        )

        assert detect_cloudflare_proxy("example.com").status == CDNProxyStatus.undetermined

    def test_a_malformed_resolved_address_is_undetermined_not_a_crash(self, mocker):
        """`validate_a_record`'s own shape check only confirms four dot-separated all-digit
        groups (`all(part.isdigit())`), so `999.999.999.999` passes it as `valid=True`. This
        module's own `ipaddress.ip_address` parse is what actually has to catch that."""
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("999.999.999.999"))

        result = detect_cloudflare_proxy("example.com")

        assert result.status == CDNProxyStatus.undetermined
        assert result.resolved_ip is None

    def test_an_unexpected_exception_from_the_validator_is_undetermined_not_raised(self, mocker):
        mocker.patch.object(DNSValidator, "validate_a_record", side_effect=RuntimeError("boom"))

        result = detect_cloudflare_proxy("example.com")

        assert result.status == CDNProxyStatus.undetermined
        assert "boom" in result.detail


@pytest.mark.unit
class TestIPv6:
    def test_a_v6_address_in_a_cloudflare_range_is_reported_proxied(self, mocker):
        """Resolution in this module is v4-only (`validate_a_record` queries A records only,
        per this module's own docstring on why AAAA is out of scope here). This test exercises
        the OTHER half: the range-membership check itself already accepts either address family,
        since it walks the vendored list -- which holds both v4 and v6 CIDRs -- with
        `ipaddress.ip_network`. A v6 value reaching that check, however it got there, is matched
        correctly, proving the v6 entries are not silently dead code."""
        mocker.patch.object(DNSValidator, "validate_a_record", return_value=_resolved("2606:4700::1"))

        result = detect_cloudflare_proxy("example.com")

        assert result.status == CDNProxyStatus.proxied
        assert result.provider == CDNProvider.cloudflare
        assert result.resolved_ip == "2606:4700::1"

    def test_no_a_record_is_undetermined_even_for_a_v6_only_domain(self, mocker):
        """The one query this module makes is for A; a domain that only publishes AAAA -- proxied
        by Cloudflare or not -- resolves no A record and must report undetermined, never
        not_proxied, because nothing was actually established about it."""
        mocker.patch.object(
            DNSValidator, "validate_a_record", return_value=_failed("No A record found for example.com")
        )

        assert detect_cloudflare_proxy("example.com").status == CDNProxyStatus.undetermined


@pytest.mark.unit
class TestNeverRaises:
    def test_no_exception_escapes_regardless_of_output_handler(self, mocker):
        """A caller that passes no output handler (the default) must be exactly as safe as one
        that passes a real one -- this is the shape `fm ssl add` will call it with."""
        mocker.patch.object(DNSValidator, "validate_a_record", side_effect=RuntimeError("dig segfaulted"))

        result = detect_cloudflare_proxy("example.com", output=MagicMock())

        assert result.status == CDNProxyStatus.undetermined
