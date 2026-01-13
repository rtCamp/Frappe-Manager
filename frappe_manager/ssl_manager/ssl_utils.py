"""
SSL Manager utility functions for credentials and configuration management.
"""
from typing import Optional, Dict
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeCredentailsNotFound


def get_dns_credentials_for_certificate(certificate: SSLCertificate) -> Optional[Dict[str, str]]:
    """
    Load DNS credentials from global config and format for acme.sh.
    
    This function retrieves Cloudflare API credentials from the global FM config
    and formats them as environment variables for acme.sh dns_cf plugin.
    
    Args:
        certificate: Certificate configuration
        
    Returns:
        Dictionary of environment variables for acme.sh:
        - {'CF_Token': 'xxx'} for API token auth, or
        - {'CF_Key': 'xxx', 'CF_Email': 'xxx'} for global API key auth
        - None if not a DNS challenge
        
    Raises:
        SSLDNSChallengeCredentailsNotFound: If DNS challenge requires credentials but none found
        
    Example:
        >>> cert = SSLCertificate(domain="example.com", challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.dns01)
        >>> creds = get_dns_credentials_for_certificate(cert)
        >>> # Use creds with acme.sh service
        >>> service.generate_certificate(cert, dns_credentials=creds)
    """
    if certificate.challenge_type != LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
        return None
    
    # Load global config
    fm_config = FMConfigManager.import_from_toml()
    
    # Map to acme.sh Cloudflare plugin format
    # https://github.com/acmesh-official/acme.sh/wiki/dnsapi#dns_cf
    credentials = {}
    
    if fm_config.letsencrypt.api_token:
        # Preferred: API Token (more secure, scoped permissions)
        credentials['CF_Token'] = fm_config.letsencrypt.api_token
    elif fm_config.letsencrypt.api_key:
        # Legacy: Global API Key (requires email)
        credentials['CF_Key'] = fm_config.letsencrypt.api_key
        if fm_config.letsencrypt.email:
            credentials['CF_Email'] = str(fm_config.letsencrypt.email)
    
    if not credentials:
        raise SSLDNSChallengeCredentailsNotFound()
    
    return credentials


def get_dns_credentials_dict(
    api_token: Optional[str] = None,
    api_key: Optional[str] = None,
    email: Optional[str] = None
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
