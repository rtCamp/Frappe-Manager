"""Characterization of certificate construction in `ssl_manager/external_domain_manager.py`.

`ExternalDomainConfigManager.to_ssl_certificate` is the only place that turns a stored
`external_domains.toml` entry into a live certificate object, and it was uncovered (~16%).
It builds the same shape that `commands/ssl/bench_helpers.py` and
`commands/ssl/external_helpers.py` build, with one extra input: `acme_client`
comes from the stored config rather than from the model default.

These tests pin the observable contract of that construction so any consolidation is provably
behaviour-preserving:

  * challenge selection is an exact string match on ``"dns01"``; EVERYTHING else -- including
    ``"DNS01"``, ``"dns-01"`` and ``""`` -- falls through to ``http01``;
  * a truthy stored ``delegation_cname`` reaches the certificate's ``delegation_cname``, a falsy
    one (None or "") leaves it None. That field, not a class, is what acme.sh dispatches on: it
    receives ``--challenge-alias`` exactly when the value is truthy;
  * `ssl_type` is always `SUPPORTED_SSL_TYPES.le`, ignoring the stored `ssl_type` string;
  * the built certificate carries no credential of any kind (they are resolved from
    ``[ssl.dns_providers]`` at issuance, so a certificate must never hold one);
  * `acme_client` is threaded through from the config with and without delegation;
  * a missing domain yields None.

Two levels are exercised: `to_ssl_certificate` in isolation (stubbing `get_domain`, so configs
that cannot survive a TOML round-trip can still be fed in) and end-to-end through a real
`external_domains.toml` under `tmp_path`. No docker, no network, no real ~/frappe.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import RETIRED_CERTIFICATE_KEYS
from frappe_manager.ssl_manager.external_domain_manager import ExternalDomainConfig, ExternalDomainConfigManager
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate

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

        assert cert.delegation_cname == "app-example-com.fm.com"
        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.dns01

    def test_unknown_challenge_in_delegation_branch_also_falls_back(self, tmp_path):
        cert = _cert_for(tmp_path, _config(challenge_type="whatever", delegation_cname="app-example-com.fm.com"))

        assert cert.delegation_cname == "app-example-com.fm.com"
        assert cert.challenge_type is LETSENCRYPT_PREFERRED_CHALLENGE.http01


# --------------------------------------------------------------------------------------
# delegation selection
# --------------------------------------------------------------------------------------


class TestDelegationSelection:
    """`delegation_cname` is a plain field, so its VALUE is the whole contract.

    acme.sh gets `--challenge-alias` when the field is truthy and not otherwise, so the empty
    string matters as much as None: a falsy value must not become a delegation.
    """

    def test_no_cname_leaves_the_delegation_unset(self, tmp_path):
        cert = _cert_for(tmp_path, _config())

        assert type(cert) is LetsencryptSSLCertificate
        assert cert.delegation_cname is None

    def test_cname_reaches_the_certificate(self, tmp_path):
        cert = _cert_for(tmp_path, _config(delegation_cname="app-example-com.fm.com"))

        assert type(cert) is LetsencryptSSLCertificate
        assert cert.delegation_cname == "app-example-com.fm.com"

    @pytest.mark.parametrize("falsy_cname", [None, ""])
    def test_falsy_cname_leaves_the_delegation_unset(self, tmp_path, falsy_cname):
        """An empty string must not become a truthy `--challenge-alias` argument."""
        cert = _cert_for(tmp_path, _config(delegation_cname=falsy_cname))

        assert not cert.delegation_cname

    def test_missing_domain_returns_none(self, tmp_path):
        manager = _manager(tmp_path)

        assert manager.to_ssl_certificate("nope.example.com") is None


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
    def test_the_certificate_carries_no_credential_at_all(self, tmp_path, cname):
        """Credentials are resolved from `[ssl.dns_providers]` at issuance, never stored here.

        A copy on the certificate is not merely redundant: it outlives revocation, and it defeated
        `fm ssl dns-config cloudflare --remove`.
        """
        cert = _cert_for(tmp_path, _config(challenge_type="dns01", delegation_cname=cname))

        assert RETIRED_CERTIFICATE_KEYS.isdisjoint(cert.model_dump())

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
        assert cert.dns_provider is None

    def test_exact_field_set_without_delegation(self, tmp_path):
        """Guards against a kwarg being silently added or dropped by a refactor."""
        cert = _cert_for(tmp_path, _config(challenge_type="dns01", acme_client="certbot"))

        assert cert.model_dump() == {
            "domain": DOMAIN,
            "ssl_type": SUPPORTED_SSL_TYPES.le,
            "challenge_type": LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            "enabled": True,
            "hsts": "off",
            "behind_proxy": False,
            "acme_client": "certbot",
            # None because a standalone certificate has no bench, so there is no bench-scoped
            # `[ssl.dns_providers]` for a label to name; `fm ssl add` refuses --dns-provider
            # together with --standalone rather than record a binding nothing reads.
            "dns_provider": None,
            "delegation_cname": None,
        }

    def test_exact_field_set_with_delegation(self, tmp_path):
        cert = _cert_for(
            tmp_path,
            _config(challenge_type="dns01", acme_client="certbot", delegation_cname="app-example-com.fm.com"),
        )

        assert cert.model_dump() == {
            "domain": DOMAIN,
            "ssl_type": SUPPORTED_SSL_TYPES.le,
            "challenge_type": LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
            "enabled": True,
            "hsts": "off",
            "behind_proxy": False,
            "acme_client": "certbot",
            "dns_provider": None,
            "delegation_cname": "app-example-com.fm.com",
        }


# --------------------------------------------------------------------------------------
# end to end through a real external_domains.toml
# --------------------------------------------------------------------------------------


class TestRoundTripThroughStorage:
    def test_added_dns01_delegated_domain_round_trips_with_its_delegation(self, tmp_path):
        manager = _manager(tmp_path)
        manager.add_domain(
            _config(challenge_type="dns01", delegation_cname="app-example-com.fm.com", acme_client="certbot")
        )

        cert = manager.to_ssl_certificate(DOMAIN)

        assert type(cert) is LetsencryptSSLCertificate
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
