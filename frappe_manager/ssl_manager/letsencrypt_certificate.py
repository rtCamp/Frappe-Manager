from typing import Optional, List
from pydantic import EmailStr, Field, model_validator
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import BaseSSLConfig
from frappe_manager.ssl_manager.certificate_exceptions import SSLDNSChallengeCredentailsNotFound
from frappe_manager.display_manager.DisplayManager import richprint


class LetsencryptConfig(BaseSSLConfig):
    preferred_challenge: LETSENCRYPT_PREFERRED_CHALLENGE
    email: EmailStr = Field(..., description="Email used by certbot.")
    api_token: Optional[str] = Field(None, description="Cloudflare API token used by Certbot.")
    api_key: Optional[str] = Field(None, description="Cloudflare Global API Key used by Certbot.")
    toml_exclude: Optional[set] = {'domain', 'alias_domains', 'toml_exclude'}

    @classmethod
    def configure(
        cls,
        domain: str,
        alias_domains: list[str] = None,
        letsencrypt_email: Optional[str] = None,
        letsencrypt_preferred_challenge: Optional[LETSENCRYPT_PREFERRED_CHALLENGE] = None,
        fm_config_manager: Optional['FMConfigManager'] = None
    ):
        """
        Configure Let's Encrypt SSL certificate for the site

        Args:
            domain: The domain for the certificate.
            letsencrypt_email: Optional email for Let's Encrypt
            letsencrypt_preferred_challenge: Preferred challenge method
            fm_config_manager: FM config manager for defaults

        Returns:
            LetsencryptConfig: Configured certificate

        Raises:
            typer.BadParameter: If required email is missing
        """
        import typer
        from email_validator import validate_email
        from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
        from frappe_manager.display_manager.DisplayManager import richprint

        import typer
        from email_validator import validate_email
        from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
        from frappe_manager.display_manager.DisplayManager import richprint

        # Determine preferred challenge
        if letsencrypt_preferred_challenge is None:
            if fm_config_manager and fm_config_manager.letsencrypt.exists:
                letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
            else:
                letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01

        # Determine email
        email = letsencrypt_email
        fm_email = getattr(fm_config_manager.letsencrypt, "email", None) if fm_config_manager else None

        if email:
            validate_email(email, check_deliverability=False)
        elif fm_email and fm_email != "dummy@fm.fm":
            richprint.print(
                "Defaulting to Let's Encrypt email from [blue]fm_config.toml[/blue] since [blue]'--letsencrypt-email'[/blue] is not given."
            )
            email = fm_email
        else:
            richprint.stop()
            raise typer.BadParameter("No email provided, required by certbot.", param_hint='--letsencrypt-email')

        return cls(
            domain=domain,
            alias_domains=alias_domains,
            ssl_type=SUPPORTED_SSL_TYPES.le,
            email=email,
            preferred_challenge=letsencrypt_preferred_challenge,
            api_key=getattr(fm_config_manager.letsencrypt, "api_key", None) if fm_config_manager else None,
            api_token=getattr(fm_config_manager.letsencrypt, "api_token", None) if fm_config_manager else None,
        )

    @model_validator(mode="after")
    def validate_credentials(self) -> 'LetsencryptConfig':
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
