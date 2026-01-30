"""
SSL Manager utility functions for credentials and configuration management.
"""

from typing import Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from frappe_manager.site_manager.bench_config import BenchConfig

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeCredentailsNotFound


def get_dns_credentials_for_certificate(
    certificate: SSLCertificate, bench_config: Optional['BenchConfig'] = None
) -> Optional[Dict[str, str]]:
    """
    Load DNS credentials and format for acme.sh.

    Checks credentials in this order:
    1. Bench-level DNS provider config (if bench_config provided)
    2. Global FM config
    3. Certificate-specific credentials (api_token/api_key on cert object)

    This function retrieves Cloudflare API credentials and formats them as
    environment variables for acme.sh dns_cf plugin.

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

    Example:
        >>> cert = SSLCertificate(domain="example.com", challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.dns01)
        >>> creds = get_dns_credentials_for_certificate(cert, bench_config)
        >>> # Use creds with acme.sh service
        >>> service.generate_certificate(cert, dns_credentials=creds)
    """
    if certificate.challenge_type != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        return None

    credentials = {}

    if bench_config and bench_config.dns_providers:
        cloudflare_config = bench_config.dns_providers.get('cloudflare')
        if cloudflare_config and cloudflare_config.exists:
            if cloudflare_config.api_token:
                credentials['CF_Token'] = cloudflare_config.api_token
            elif cloudflare_config.api_key:
                credentials['CF_Key'] = cloudflare_config.api_key
                if cloudflare_config.email:
                    credentials['CF_Email'] = str(cloudflare_config.email)
            if credentials:
                return credentials

    fm_config = FMConfigManager.import_from_toml()

    if fm_config.cloudflare.api_token:
        credentials['CF_Token'] = fm_config.cloudflare.api_token
    elif fm_config.cloudflare.api_key:
        credentials['CF_Key'] = fm_config.cloudflare.api_key
        if fm_config.cloudflare.email:
            credentials['CF_Email'] = str(fm_config.cloudflare.email)

    if not credentials:
        if hasattr(certificate, 'api_token') and certificate.api_token:
            credentials['CF_Token'] = certificate.api_token
        elif hasattr(certificate, 'api_key') and certificate.api_key:
            credentials['CF_Key'] = certificate.api_key
            if hasattr(certificate, 'email') and certificate.email:
                credentials['CF_Email'] = str(certificate.email)

    if not credentials:
        raise SSLDNSChallengeCredentailsNotFound()

    return credentials


def get_dns_credentials_dict(
    api_token: Optional[str] = None, api_key: Optional[str] = None, email: Optional[str] = None
) -> Dict[str, str]:
    """
    Create DNS credentials dictionary from individual parameters.

    Useful for explicitly passing credentials instead of loading from config.

    Args:
        api_token: Cloudflare API token (preferred)
        api_key: Cloudflare Global API key (legacy)
        email: Email for Global API key auth

    Returns:
        Dictionary formatted for acme.sh

    Raises:
        SSLDNSChallengeCredentailsNotFound: If no valid credentials provided

    Example:
        >>> creds = get_dns_credentials_dict(api_token="my_token_here")
        >>> service.generate_certificate(cert, dns_credentials=creds)
    """
    credentials = {}

    if api_token:
        credentials['CF_Token'] = api_token
    elif api_key:
        credentials['CF_Key'] = api_key
        if email:
            credentials['CF_Email'] = email

    if not credentials:
        raise SSLDNSChallengeCredentailsNotFound(
            "No Cloudflare credentials provided. Need either api_token or (api_key + email)"
        )

    return credentials
