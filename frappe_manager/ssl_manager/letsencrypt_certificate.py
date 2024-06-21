from typing import Optional, List, Self
from pydantic import EmailStr, Field, model_validator
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeCredentailsNotFound
from frappe_manager.display_manager.DisplayManager import richprint


class LetsencryptSSLCertificate(SSLCertificate):
    preferred_challenge: LETSENCRYPT_PREFERRED_CHALLENGE
    email: EmailStr = Field(..., description="Email used by certbot.")
    api_token: Optional[str] = Field(None, description="Cloudflare API token used by Certbot.")
    api_key: Optional[str] = Field(None, description="Cloudflare Global API Key used by Certbot.")
    toml_exclude: Optional[set] = {'domain', 'alias_domains', 'toml_exclude'}

    @model_validator(mode="after")
    def validate_credentials(self) -> Self:
        if self.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
            if self.api_key or self.api_token:
                return self
            else:
                raise SSLDNSChallengeCredentailsNotFound()

        return self

    def get_cloudflare_dns_credentials(self) -> str:
        creds: List[str] = []

        if self.api_key:
            richprint.print('Using Cloudflare GLOBAL API KEY')
            creds.append(f'dns_cloudflare_email = {self.email}\n')
            creds.append(f'dns_cloudflare_api_key = {self.api_key}\n')

        if self.api_token:
            richprint.print('Using Cloudflare API Token')
            creds.append(f'dns_cloudflare_api_token = {self.api_token}\n')

        if not creds:
            raise SSLDNSChallengeCredentailsNotFound()

        return "\n".join(creds)
