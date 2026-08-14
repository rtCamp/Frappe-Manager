"""Characterization of certificate construction in `ssl_manager/external_domain_manager.py`.

`ExternalDomainConfigManager.to_ssl_certificate` is the only place that turns a stored
`external_domains.toml` entry into a live certificate object, and it was uncovered (~16%).
It builds the same two-branch shape that `commands/ssl/bench_helpers.py` and
`commands/ssl/external_helpers.py` used to duplicate, with one extra input: `acme_client`
comes from the stored config rather than from the model default.

These tests pin the observable contract of that construction so any consolidation is provably
behaviour-preserving:

  * challenge selection is an exact string match on ``"dns01"``; EVERYTHING else -- including
    ``"DNS01"``, ``"dns-01"`` and ``""`` -- falls through to ``http01``;
  * a truthy ``delegation_cname`` selects `CustomDomainCertificate`, a falsy one (None or "")
    selects `LetsencryptSSLCertificate`; the exact class matters, not just isinstance, because
    `CustomDomainCertificate` is a subclass;
  * `ssl_type` is always `SUPPORTED_SSL_TYPES.le`, ignoring the stored `ssl_type` string;
  * `api_token`/`api_key` are always None (credentials are resolved from fm config at issuance);
  * `acme_client` is threaded through from the config in BOTH branches;
  * a missing domain yields None.

Two levels are exercised: `to_ssl_certificate` in isolation (stubbing `get_domain`, so configs
that cannot survive a TOML round-trip can still be fed in) and end-to-end through a real
`external_domains.toml` under `tmp_path`. No docker, no network, no real ~/frappe.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.external_domain_manager import ExternalDomainConfig, ExternalDomainConfigManager
from frappe_manager.ssl_manager.letsencrypt_certificate import CustomDomainCertificate, LetsencryptSSLCertificate

DOMAIN = "app.example.com"
ADDED_AT = "2026-01-14T12:00:00"


# --------------------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------------------


def _config(**overrides) -> ExternalDomainConfig:
    """A stored config with the documented defaults, overridable per test."""
    kwargs = {
        "domain": DOMAIN,
        "ssl_type": "letsencrypt",
        "added_at": ADDED_AT,
        "challenge_type": "http01",
    }
    kwargs.update(overrides)
    return ExternalDomainConfig(**kwargs)


def _manager(tmp_path: Path) -> ExternalDomainConfigManager:
    return ExternalDomainConfigManager(tmp_path / "nginx-proxy" / "external_domains.toml")


def _cert_for(tmp_path: Path, config: ExternalDomainConfig):
    """Run `to_ssl_certificate` against an exact in-memory config, bypassing TOML storage."""
    manager = _manager(tmp_path)
    with patch.object(manager, "get_domain", return_value=config) as get_domain:
        cert = manager.to_ssl_certificate(config.domain)
    get_domain.assert_called_once_with(config.domain)
    return cert


# --------------------------------------------------------------------------------------
# challenge-type selection
# --------------------------------------------------------------------------------------


class TestChallengeTypeSelection:
    """`config.challenge_type` (a plain string) -> `LETSENCRYPT_PREFERRED_CHALLENGE`."""

    def test_dns01_string_selects_dns01_challenge(self, tmp_path):
        cert = _cert_for(tmp_path, _config(challenge_type="dns01"))

        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.dns01

    def test_http01_string_selects_http01_challenge(self, tmp_path):
        cert = _cert_for(tmp_path, _config(challenge_type="http01"))

        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.http01

    @pytest.mark.parametrize(
        "stored",
        [
            "DNS01",
            "Dns01",
            "dns-01",
            " dns01",
            "dns01 ",
            "tls-alpn-01",
            "",
            "http-01",
            "garbage",
        ],
    )
    def test_anything_other_than_exact_dns01_falls_back_to_http01(self, tmp_path, stored):
        """Selection is an exact `== "dns01"` match; there is no normalisation or validation."""
        cert = _cert_for(tmp_path, _config(challenge_type=stored))

        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.http01, (
            f"stored challenge_type {stored!r} must fall through to http01"
        )

    def test_challenge_selection_applies_to_the_delegation_branch_too(self, tmp_path):
        cert = _cert_for(tmp_path, _config(challenge_type="dns01", delegation_cname="app-example-com.fm.com"))

        assert type(cert) is CustomDomainCertificate
        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.dns01

    def test_unknown_challenge_in_delegation_branch_also_falls_back(self, tmp_path):
        cert = _cert_for(tmp_path, _config(challenge_type="whatever", delegation_cname="app-example-com.fm.com"))

        assert type(cert) is CustomDomainCertificate
        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.http01


# --------------------------------------------------------------------------------------
# branch / class selection
# --------------------------------------------------------------------------------------


class TestCertificateClassSelection:
    def test_no_cname_builds_plain_letsencrypt_certificate(self, tmp_path):
        cert = _cert_for(tmp_path, _config())

        assert type(cert) is LetsencryptSSLCertificate
        assert not isinstance(cert, CustomDomainCertificate)
        assert not hasattr(cert, "delegation_cname")

    def test_cname_builds_custom_domain_certificate_and_keeps_the_cname(self, tmp_path):
        cert = _cert_for(tmp_path, _config(delegation_cname="app-example-com.fm.com"))

        assert type(cert) is CustomDomainCertificate
        assert cert.delegation_cname == "app-example-com.fm.com"

    @pytest.mark.parametrize("falsy_cname", [None, ""])
    def test_falsy_cname_builds_plain_certificate(self, tmp_path, falsy_cname):
        """The branch is a truthiness check, so an empty string behaves like None."""
        cert = _cert_for(tmp_path, _config(delegation_cname=falsy_cname))

        assert type(cert) is LetsencryptSSLCertificate

    def test_missing_domain_returns_none(self, tmp_path):
        manager = _manager(tmp_path)

        assert manager.to_ssl_certificate("nope.example.com") is None

    def test_delegation_subdomain_is_derived_from_the_domain(self, tmp_path):
        cert = _cert_for(tmp_path, _config(delegation_cname="app-example-com.fm.com"))

        assert cert.get_delegation_subdomain() == "app-example-com"


# --------------------------------------------------------------------------------------
# field-by-field shape of the built object
# --------------------------------------------------------------------------------------


class TestCertificateFields:
    @pytest.mark.parametrize("cname", [None, "app-example-com.fm.com"])
    def test_domain_is_taken_from_the_stored_config(self, tmp_path, cname):
        cert = _cert_for(tmp_path, _config(domain="other.example.com", delegation_cname=cname))

        assert cert.domain == "other.example.com"

    @pytest.mark.parametrize("cname", [None, "app-example-com.fm.com"])
    def test_ssl_type_is_always_letsencrypt(self, tmp_path, cname):
        """The stored `ssl_type` string is ignored; the enum member is hardcoded."""
        cert = _cert_for(tmp_path, _config(ssl_type="something-else", delegation_cname=cname))

        assert cert.ssl_type is SUPPORTED_SSL_TYPES.le

    @pytest.mark.parametrize("cname", [None, "app-example-com.fm.com"])
    def test_credentials_are_never_populated(self, tmp_path, cname):
        """Cloudflare credentials are resolved from fm config at issuance, not stored here."""
        cert = _cert_for(tmp_path, _config(challenge_type="dns01", delegation_cname=cname))

        assert cert.api_token is None
        assert cert.api_key is None

    @pytest.mark.parametrize("cname", [None, "app-example-com.fm.com"])
    def test_acme_client_comes_from_the_stored_config(self, tmp_path, cname):
        """This is the one input the command-layer builder does not have."""
        cert = _cert_for(tmp_path, _config(acme_client="certbot", delegation_cname=cname))

        assert cert.acme_client == "certbot"

    @pytest.mark.parametrize("cname", [None, "app-example-com.fm.com"])
    def test_acme_client_default_is_acme_sh(self, tmp_path, cname):
        cert = _cert_for(tmp_path, _config(delegation_cname=cname))

        assert cert.acme_client == "acme.sh"

    @pytest.mark.parametrize("cname", [None, "app-example-com.fm.com"])
    def test_system_state_fields_are_left_at_their_model_defaults(self, tmp_path, cname):
        cert = _cert_for(tmp_path, _config(delegation_cname=cname))

        assert cert.enabled is True
        assert cert.hsts == "off"
        assert cert.status == "pending"
        assert cert.cert_path is None
        assert cert.key_path is None
        assert cert.issued_date is None
        assert cert.last_renewal_attempt is None

    def test_exact_field_set_of_the_plain_branch(self, tmp_path):
        """Guards against a kwarg being silently added or dropped by a refactor."""
        cert = _cert_for(tmp_path, _config(challenge_type="dns01", acme_client="certbot"))

        assert cert.model_dump() == {
            "domain": DOMAIN,
            "ssl_type": SUPPORTED_SSL_TYPES.le,
            "challenge_type": LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            "enabled": True,
            "acme_client": "certbot",
            "hsts": "off",
            "cert_path": None,
            "key_path": None,
            "issued_date": None,
            "last_renewal_attempt": None,
            "status": "pending",
            "toml_exclude": {"domain", "toml_exclude"},
            "api_token": None,
            "api_key": None,
        }

    def test_exact_field_set_of_the_delegation_branch(self, tmp_path):
        cert = _cert_for(
            tmp_path,
            _config(challenge_type="dns01", acme_client="certbot", delegation_cname="app-example-com.fm.com"),
        )

        assert cert.model_dump() == {
            "domain": DOMAIN,
            "ssl_type": SUPPORTED_SSL_TYPES.le,
            "challenge_type": LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            "enabled": True,
            "acme_client": "certbot",
            "hsts": "off",
            "cert_path": None,
            "key_path": None,
            "issued_date": None,
            "last_renewal_attempt": None,
            "status": "pending",
            "toml_exclude": {"domain", "toml_exclude"},
            "api_token": None,
            "api_key": None,
            "delegation_cname": "app-example-com.fm.com",
        }


# --------------------------------------------------------------------------------------
# end to end through a real external_domains.toml
# --------------------------------------------------------------------------------------


class TestRoundTripThroughStorage:
    def test_added_dns01_delegated_domain_round_trips_to_a_custom_certificate(self, tmp_path):
        manager = _manager(tmp_path)
        manager.add_domain(
            _config(challenge_type="dns01", delegation_cname="app-example-com.fm.com", acme_client="certbot")
        )

        cert = manager.to_ssl_certificate(DOMAIN)

        assert type(cert) is CustomDomainCertificate
        assert cert.domain == DOMAIN
        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.dns01
        assert cert.delegation_cname == "app-example-com.fm.com"
        assert cert.acme_client == "certbot"

    def test_added_http01_domain_round_trips_to_a_plain_certificate(self, tmp_path):
        manager = _manager(tmp_path)
        manager.add_domain(_config())

        cert = manager.to_ssl_certificate(DOMAIN)

        assert type(cert) is LetsencryptSSLCertificate
        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.http01
        assert cert.acme_client == "acme.sh"

    def test_removed_domain_no_longer_yields_a_certificate(self, tmp_path):
        manager = _manager(tmp_path)
        manager.add_domain(_config())

        assert manager.remove_domain(DOMAIN) is True
        assert manager.to_ssl_certificate(DOMAIN) is None

    def test_legacy_preferred_challenge_key_still_selects_dns01(self, tmp_path):
        """`_load` renames the pre-rename key, so old files keep their challenge."""
        config_path = tmp_path / "nginx-proxy" / "external_domains.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "[domains.app_example_com]\n"
            f'domain = "{DOMAIN}"\n'
            'ssl_type = "letsencrypt"\n'
            f'added_at = "{ADDED_AT}"\n'
            'preferred_challenge = "dns01"\n'
            'email = "legacy@example.com"\n'
        )

        cert = ExternalDomainConfigManager(config_path).to_ssl_certificate(DOMAIN)

        assert type(cert) is LetsencryptSSLCertificate
        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.dns01
        assert cert.acme_client == "acme.sh"

    def test_empty_cname_is_dropped_by_storage_and_yields_a_plain_certificate(self, tmp_path):
        manager = _manager(tmp_path)
        manager.add_domain(_config(delegation_cname=""))

        assert manager.get_domain(DOMAIN).delegation_cname is None
        assert type(manager.to_ssl_certificate(DOMAIN)) is LetsencryptSSLCertificate
