"""
SSL Manager utility functions for credentials and configuration management.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from frappe_manager.site_manager.bench_config import BenchConfig

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.ssl_manager import DNS_PROVIDER, LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLDNSChallengeCredentailsNotFound,
    SSLDNSProviderNotConfigured,
)
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig


def resolve_dns_provider(
    certificate: SSLCertificate,
    bench_config: Optional["BenchConfig"] = None,
) -> DNSProviderConfig | None:
    """
    Resolve which credential set authenticates this certificate's DNS-01 challenge.

    Bench labels, then global labels; a named-but-missing label is an error, never a fallback.

    Falling back would hide the mistake in the worst possible way. Before this function existed the
    lookup was a hardcoded `dns_providers.get("cloudflare")`, so a certificate bound to a second
    Cloudflare account silently authenticated with the FIRST account's token: two labelled providers
    were configured, both certificates resolved to the first token, and the challenge then failed
    with an opaque acme.sh error that named neither the account nor the label.

    Args:
        certificate: Certificate configuration; its `dns_provider` names the label, if any
        bench_config: Optional bench configuration, holding the bench-scoped labels

    Returns:
        The matching credential set, or None when nothing is configured anywhere

    Raises:
        SSLDNSProviderNotConfigured: If the certificate names a label that no scope configures
    """
    label = getattr(certificate, "dns_provider", None)

    bench_labels: dict[str, DNSProviderConfig] = {}
    if bench_config is not None:
        bench_labels = bench_config.dns_providers or {}

    wanted = label or DNS_PROVIDER.cloudflare.value

    provider = bench_labels.get(wanted)
    if provider and provider.exists:
        return provider

    # Deferred so a bench-scoped hit never reads the global config off disk.
    fm_config = FMConfigManager.import_from_toml()
    global_labels = fm_config.dns_providers or {}

    provider = global_labels.get(wanted)
    if provider and provider.exists:
        return provider

    if label:
        raise SSLDNSProviderNotConfigured(label, sorted(set(bench_labels) | set(global_labels)))

    return None


def get_dns_credentials_for_certificate(
    certificate: SSLCertificate,
    bench_config: Optional["BenchConfig"] = None,
) -> dict[str, str] | None:
    """
    Load DNS credentials and format for acme.sh.

    The credential set comes from `resolve_dns_provider`, which picks it by label across the bench
    and global scopes; this function only shapes it into acme.sh's dns_cf environment variables.

    Args:
        certificate: Certificate configuration
        bench_config: Optional bench configuration for bench-level credentials

    Returns:
        Dictionary of environment variables for acme.sh:
        - {'CF_Token': 'xxx'} for API token auth, or
        - {'CF_Key': 'xxx', 'CF_Email': 'xxx'} for global API key auth
        - None if not a DNS challenge

    Raises:
        SSLDNSChallengeCredentailsNotFound: If DNS challenge requires credentials but none found
        SSLDNSProviderNotConfigured: If the certificate names a label that no scope configures

    Example:
        >>> cert = SSLCertificate(domain="example.com", challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.dns01)
        >>> creds = get_dns_credentials_for_certificate(cert, bench_config)
        >>> env.update(creds or {})
    """
    if certificate.challenge_type != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        return None

    provider = resolve_dns_provider(certificate, bench_config)

    if provider is None:
        raise SSLDNSChallengeCredentailsNotFound()

    credentials: dict[str, str] = {}

    if provider.provider is DNS_PROVIDER.cloudflare:
        # A resolved set always satisfies `.exists`, i.e. holds a token or a key, so one branch fires.
        if provider.api_token:
            credentials["CF_Token"] = provider.api_token
        elif provider.api_key:
            credentials["CF_Key"] = provider.api_key
            if provider.email:
                credentials["CF_Email"] = str(provider.email)
        return credentials

    # DNS_PROVIDER is a closed enum and every member has to be handled here, because the CF_* names
    # above are Cloudflare's own: a new member falling through would hand a foreign token to acme.sh's
    # dns_cf plugin. Unreachable from a config file today, since the field is typed as the enum and a
    # `provider = "route53"` is refused at load, so this guards the next member rather than the user.
    raise ValueError(f"No acme.sh credential mapping for DNS provider {provider.provider.value}")
