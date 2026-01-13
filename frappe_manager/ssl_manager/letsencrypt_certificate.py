from typing import Optional
from pydantic import EmailStr, Field, model_validator

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeCredentailsNotFound


class LetsencryptSSLCertificate(SSLCertificate):
    email: EmailStr = Field(..., description="Email used for Let's Encrypt notifications.")
    api_token: str | None = Field(None, description="Cloudflare API token.")
    api_key: str | None = Field(None, description="Cloudflare Global API Key.")
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

    def get_cloudflare_dns_credentials(self, output_handler: OutputHandler | None = None) -> str:
        output = output_handler or RichOutputHandler()
        creds: list[str] = []

        if self.api_key:
            output.print("Using Cloudflare GLOBAL API KEY")
            creds.append(f"dns_cloudflare_email = {self.email}\n")
            creds.append(f"dns_cloudflare_api_key = {self.api_key}\n")

        if self.api_token:
            output.print("Using Cloudflare API Token")
            creds.append(f"dns_cloudflare_api_token = {self.api_token}\n")

        if not creds:
            raise SSLDNSChallengeCredentailsNotFound()

        return "\n".join(creds)


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

    delegation_cname: Optional[str] = None  # e.g., a-gg-com.fm.com

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
