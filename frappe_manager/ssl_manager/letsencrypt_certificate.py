from pydantic import Field, model_validator

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate


class LetsencryptSSLCertificate(SSLCertificate):
    # Email field removed - Let's Encrypt discontinued email notifications (June 2025)
    api_token: str | None = Field(None, description="Cloudflare API token.")
    api_key: str | None = Field(None, description="Cloudflare Global API Key.")
    # Let's Encrypt always uses an ACME challenge; default so downstream
    # acme.sh code can safely read challenge_type.value.
    challenge_type: LETSENCRYPT_PREFERRED_CHALLENGE = LETSENCRYPT_PREFERRED_CHALLENGE.http01
    toml_exclude: set | None = {"domain", "toml_exclude"}

    @model_validator(mode="after")
    def validate_credentials(self) -> "LetsencryptSSLCertificate":
        """
        Validate DNS-01 credentials at certificate creation time.

        Note: Credentials are loaded at runtime from FM config via
        get_dns_credentials_for_certificate(), so we only validate if
        credentials are provided on the cert object itself (backward compat).
        The actual credential availability check happens at certificate
        generation time in acmesh_certificate_service.py.
        """
        # Skip validation - credentials are loaded dynamically from FM config
        # The acme.sh service will validate credentials when generating the cert
        return self


class CustomDomainCertificate(LetsencryptSSLCertificate):
    """
    Certificate for custom domain with CNAME delegation.

    Used for delegated DNS validation pattern where user creates CNAME
    pointing to a domain we control.

    Example:
        User domain: a.gg.com
        CNAME: a.gg.com → a-gg-com.fm.com
        Challenge validation: _acme-challenge.a-gg-com.fm.com

    This allows issuing certificates for customer domains without requiring
    direct access to their DNS provider.
    """

    delegation_cname: str | None = None  # e.g., a-gg-com.fm.com

    @model_validator(mode="after")
    def validate_credentials(self) -> "CustomDomainCertificate":
        """
        Override parent validation - CNAME delegation doesn't require credentials
        for the custom domain itself. DNS validation happens on the delegated domain.
        """
        # Skip validation for CNAME delegation - credentials are for the delegated domain
        return self

    def get_delegation_subdomain(self) -> str:
        """
        Generate delegation subdomain from domain name.

        Converts dots to hyphens for use in delegation CNAME.

        Example:
            a.gg.com -> a-gg-com

        Returns:
            Hyphenated domain name suitable for subdomain use
        """
        return self.domain.replace(".", "-")


def build_letsencrypt_certificate(
    domain: str,
    challenge: LETSENCRYPT_PREFERRED_CHALLENGE,
    cname: str | None,
    acme_client: str | None = None,
) -> LetsencryptSSLCertificate:
    """Build the Let's Encrypt certificate for a domain; a cname picks the delegating subclass.

    Credentials are deliberately left unset -- they are resolved from fm config at issuance.

    Lives here, in ssl_manager, rather than in the command layer: the command modules and
    ``external_domain_manager`` all build this identical object, and a home under
    ``frappe_manager.commands`` cannot serve the third one. That is not merely a layering
    preference -- importing the command layer from ``ssl_manager.external_domain_manager`` is a hard
    circular import (commands.ssl.helpers -> ... -> external_domain_manager, which is still
    partially initialised), verified by ImportError.

    ``acme_client`` is omitted rather than passed as None when absent: the field is a defaulted
    ``str`` ("acme.sh"), so forwarding None would replace the default instead of keeping it.
    """
    common = {"domain": domain, "ssl_type": SUPPORTED_SSL_TYPES.le, "api_token": None, "api_key": None}
    if acme_client is not None:
        common["acme_client"] = acme_client
    if cname:
        return CustomDomainCertificate(**common, challenge_type=challenge, delegation_cname=cname)
    return LetsencryptSSLCertificate(**common, challenge_type=challenge)
