
from pydantic import EmailStr, Field, model_validator

from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeCredentailsNotFound


class LetsencryptSSLCertificate(SSLCertificate):
    preferred_challenge: LETSENCRYPT_PREFERRED_CHALLENGE
    email: EmailStr = Field(..., description="Email used by certbot.")
    api_token: str | None = Field(None, description="Cloudflare API token used by Certbot.")
    api_key: str | None = Field(None, description="Cloudflare Global API Key used by Certbot.")
    toml_exclude: set | None = {"domain", "toml_exclude"}

    @model_validator(mode="after")
    def validate_credentials(self) -> "LetsencryptSSLCertificate":
        if self.preferred_challenge == LETSENCRYPT_PREFERRED_CHALLENGE.dns01:
            if self.api_key or self.api_token:
                return self
            raise SSLDNSChallengeCredentailsNotFound()

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
