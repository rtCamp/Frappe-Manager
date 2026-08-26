"""Which credential set authenticates a DNS-01 challenge, and what happens when the answer is wrong.

The resolver had no direct coverage while it hardcoded `dns_providers.get("cloudflare")`, which is how
a bench holding two Cloudflare accounts silently issued both certificates against the first token: the
second entry stored fine, round-tripped fine, and was unreachable. Every test here pins one rung of
the lookup, and the first one is the bug itself.

The global config is a real `fm_config.toml` parsed by the real loader rather than a hand-built stub,
because the labelled global table is new and its parsing is part of the contract under test.
"""

from unittest.mock import patch

import pytest

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.ssl_manager import DNS_PROVIDER, LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLDNSChallengeCredentailsNotFound,
    SSLDNSProviderNotConfigured,
)
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig
from frappe_manager.ssl_manager.letsencrypt_certificate import build_letsencrypt_certificate
from frappe_manager.ssl_manager.ssl_utils import get_dns_credentials_for_certificate, resolve_dns_provider


def _global_config(tmp_path, body: str = ""):
    """Parse a real global config file, then make the resolver read it instead of the developer's own."""
    path = tmp_path / "fm_config.toml"
    path.write_text('version = "0.20.0.dev0"\n' + body)
    parsed = FMConfigManager.import_from_toml(path)
    return patch.object(FMConfigManager, "import_from_toml", staticmethod(lambda *a, **k: parsed))


def _bench(tmp_path, **labels: DNSProviderConfig) -> BenchConfig:
    return BenchConfig(
        name="x.localhost",
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        root_path=tmp_path / "bench_config.toml",
        dns_providers=dict(labels) or None,
    )


def _cert(dns_provider: str | None = None, challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01):
    return build_letsencrypt_certificate("a.example.com", challenge, None, dns_provider=dns_provider)


def _token(token: str) -> DNSProviderConfig:
    return DNSProviderConfig(provider=DNS_PROVIDER.cloudflare, api_token=token)


def test_two_labels_on_one_bench_resolve_to_their_own_tokens(tmp_path):
    """The defect this whole surface exists to fix: both certificates used to get the first token."""
    bench = _bench(tmp_path, cloudflare=_token("tok-OURS"), client=_token("tok-CLIENT"))

    with _global_config(tmp_path):
        ours = get_dns_credentials_for_certificate(_cert(), bench)
        theirs = get_dns_credentials_for_certificate(_cert("client"), bench)

    assert ours == {"CF_Token": "tok-OURS"}
    assert theirs == {"CF_Token": "tok-CLIENT"}


def test_a_bench_label_wins_over_the_same_label_globally(tmp_path):
    """Bench scope is the override; a shared label must not shadow a bench's own credential."""
    bench = _bench(tmp_path, client=_token("tok-BENCH"))
    body = '[ssl.dns_providers.client]\nprovider = "cloudflare"\napi_token = "tok-GLOBAL"\n'

    with _global_config(tmp_path, body):
        assert get_dns_credentials_for_certificate(_cert("client"), bench) == {"CF_Token": "tok-BENCH"}


def test_a_label_configured_only_globally_is_reachable(tmp_path):
    """The point of Option B: one credential, stored once, usable from every bench."""
    bench = _bench(tmp_path)
    body = '[ssl.dns_providers.client]\nprovider = "cloudflare"\napi_token = "tok-GLOBAL"\n'

    with _global_config(tmp_path, body):
        assert get_dns_credentials_for_certificate(_cert("client"), bench) == {"CF_Token": "tok-GLOBAL"}


def test_the_api_key_shape_still_resolves(tmp_path):
    """A Global API Key needs CF_Email alongside CF_Key, unlike a scoped token."""
    body = '[ssl.dns_providers.cloudflare]\nemail = "ops@example.com"\napi_key = "key-LEGACY"\n'

    with _global_config(tmp_path, body):
        creds = get_dns_credentials_for_certificate(_cert(), _bench(tmp_path))

    assert creds == {"CF_Key": "key-LEGACY", "CF_Email": "ops@example.com"}


def test_a_named_label_is_never_satisfied_by_the_default_set(tmp_path):
    """Substituting a different account is the failure this design refuses to make quietly."""
    body = '[ssl.dns_providers.cloudflare]\napi_token = "tok-DEFAULT"\n'

    with _global_config(tmp_path, body), pytest.raises(SSLDNSProviderNotConfigured):
        get_dns_credentials_for_certificate(_cert("client"), _bench(tmp_path))


def test_a_missing_label_names_itself_and_lists_the_ones_that_exist(tmp_path):
    """The operator has to learn which label they meant, so both scopes are reported."""
    bench = _bench(tmp_path, cloudflare=_token("tok-OURS"))
    body = '[ssl.dns_providers.client]\napi_token = "tok-GLOBAL"\n'

    with _global_config(tmp_path, body), pytest.raises(SSLDNSProviderNotConfigured) as exc:
        resolve_dns_provider(_cert("typo"), bench)

    assert "typo" in str(exc.value)
    assert "client" in str(exc.value)
    assert "cloudflare" in str(exc.value)


def test_a_label_that_holds_no_credential_raises_rather_than_falling_back(tmp_path):
    """An entry with only an email satisfies nothing, and falling past it would pick another account."""
    bench = _bench(tmp_path, client=DNSProviderConfig(email="ops@example.com"), cloudflare=_token("tok-OURS"))

    with _global_config(tmp_path), pytest.raises(SSLDNSProviderNotConfigured):
        resolve_dns_provider(_cert("client"), bench)


def test_an_http01_certificate_needs_no_credential_at_all(tmp_path):
    """Resolution must not run for http01, which is why a bench with no DNS config still issues."""
    cert = _cert("client", challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01)

    with _global_config(tmp_path):
        assert get_dns_credentials_for_certificate(cert, _bench(tmp_path)) is None


def test_nothing_configured_anywhere_is_reported_as_a_missing_credential(tmp_path):
    """Distinct from a bad label: there is no label to blame, so the generic error is correct."""
    with _global_config(tmp_path), pytest.raises(SSLDNSChallengeCredentailsNotFound):
        get_dns_credentials_for_certificate(_cert(), _bench(tmp_path))


def test_a_certificate_without_the_label_field_still_resolves(tmp_path):
    """`resolve_dns_provider` is handed base certificates too, which carry no `dns_provider`."""
    bench = _bench(tmp_path, cloudflare=_token("tok-OURS"))
    dev_cert = SSLCertificate(
        domain="a.example.com",
        ssl_type=SUPPORTED_SSL_TYPES.dev,
        challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
    )

    with _global_config(tmp_path):
        assert resolve_dns_provider(dev_cert, bench).api_token == "tok-OURS"


def test_no_bench_config_at_all_uses_the_global_scope(tmp_path):
    """Standalone certificates (`fm ssl add --standalone`) have no bench to carry labels."""
    body = '[ssl.dns_providers.cloudflare]\napi_token = "tok-GLOBAL"\n'

    with _global_config(tmp_path, body):
        assert get_dns_credentials_for_certificate(_cert()) == {"CF_Token": "tok-GLOBAL"}
