from pathlib import Path
import json
from typing import TYPE_CHECKING, Optional, Dict, Any
import typer
from email_validator import validate_email
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.site_manager.site_exceptions import SiteCertificateException

if TYPE_CHECKING:
    from frappe_manager.site_manager.bench import Bench

class Site:
    def __init__(self, name: str, bench: 'Bench'):
        """Initialize a site within a bench
        
        Args:
            name: Site name
            bench: Parent Bench instance that owns this site
        """
        self.name = name
        self._bench = bench  # Store bench reference but mark as private

        self.site_dir = self._bench.path / "workspace/frappe-bench/sites" / name
        self.config_path = self.site_dir / "site_config.json"
        self.certificate: Optional[SSLCertificate] = None
        self.certificate_manager: Optional[SSLCertificateManager] = None

    @property
    def exists(self) -> bool:
        return self.site_dir.exists() and self.config_path.exists()

    def get_config(self) -> Dict[str, Any]:
        """Get site-specific configuration"""
        if not self.config_path.exists():
            return {}
        return json.loads(self.config_path.read_text())

    def set_config(self, config: Dict[str, Any]) -> None:
        """Update site-specific configuration"""
        current_config = self.get_config()
        current_config.update(config)
        self.config_path.write_text(json.dumps(current_config, indent=4))

    def get_db_name(self) -> Optional[str]:
        """Get database name from site config"""
        config = self.get_config()

        return config.get("db_name")

    def setup_certificate_manager(self, webroot_dir: Path, proxy_manager: Any):
        """Initialize certificate manager with required parameters"""
        self.certificate_manager = SSLCertificateManager(
            certificate=self.certificate,
            webroot_dir=webroot_dir,
            proxy_manager=proxy_manager
        )

    def create_certificate(self):
        """Generate SSL certificate for the site"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.name, "Certificate manager not initialized")
        self.certificate_manager.generate_certificate()
        self._save_certificate_config()

    def has_certificate(self) -> bool:
        """Check if site has SSL certificate"""
        return self.certificate_manager.has_certificate() if self.certificate_manager else False

    def remove_certificate(self):
        """Remove SSL certificate from the site"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.name, "Certificate manager not initialized")
        self.certificate_manager.remove_certificate()
        self.certificate = SSLCertificate(domain=self.name, ssl_type=SUPPORTED_SSL_TYPES.none)
        self._save_certificate_config()

    def update_certificate(self, certificate: SSLCertificate, raise_error: bool = True) -> bool:
        """Update site's SSL certificate"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.name, "Certificate manager not initialized")

        if certificate.ssl_type == SUPPORTED_SSL_TYPES.le:
            if self.has_certificate():
                if raise_error:
                    raise SiteCertificateException(self.name, "Certificate already issued")
            else:
                self.certificate_manager.set_certificate(certificate)
                self.certificate = certificate
                self.create_certificate()

        elif certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            if self.has_certificate():
                self.remove_certificate()
            else:
                if not raise_error:
                    return False
                raise SiteCertificateException(self.name, "No certificate issued")

        return True

    def renew_certificate(self):
        """Renew site's SSL certificate"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.name, "Certificate manager not initialized")
        if not self.has_certificate():
            raise SiteCertificateException(self.name, "No certificate issued")
        self.certificate_manager.renew_certificate()

    def _save_certificate_config(self):
        """Save certificate configuration to site config"""
        config = self.get_config()
        config['ssl'] = self.certificate.model_dump() if self.certificate else None
        self.set_config(config)

    def configure_letsencrypt(self, 
        letsencrypt_email: Optional[str] = None,
        letsencrypt_preferred_challenge: Optional[LETSENCRYPT_PREFERRED_CHALLENGE] = None,
        fm_config_manager: Optional['FMConfigManager'] = None
    ) -> 'LetsencryptSSLCertificate':
        """
        Configure Let's Encrypt SSL certificate for the site
        
        Args:
            letsencrypt_email: Optional email for Let's Encrypt
            letsencrypt_preferred_challenge: Preferred challenge method
            fm_config_manager: FM config manager for defaults
            
        Returns:
            LetsencryptSSLCertificate: Configured certificate
        
        Raises:
            typer.BadParameter: If required email is missing
        """
        if not letsencrypt_preferred_challenge:
            if fm_config_manager and fm_config_manager.letsencrypt.exists:
                letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
            if not letsencrypt_preferred_challenge:
                letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01

        # Handle email configuration
        email = letsencrypt_email
        if fm_config_manager.letsencrypt.email == 'dummy@fm.fm' or fm_config_manager.letsencrypt.email is None:
            if not email:
                richprint.stop()
                raise typer.BadParameter("No email provided, required by certbot.", param_hint='--letsencrypt-email')
            validate_email(email, check_deliverability=False)
        else:
            richprint.print(
                "Defaulting to Let's Encrypt email from [blue]fm_config.toml[/blue] since [blue]'--letsencrypt-email'[/blue] is not given."
            )
            email = fm_config_manager.letsencrypt.email

        # Create and return certificate
        from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
        return LetsencryptSSLCertificate(
            domain=self.name,
            ssl_type=SUPPORTED_SSL_TYPES.le,
            email=email,
            preferred_challenge=letsencrypt_preferred_challenge,
            api_key=fm_config_manager.letsencrypt.api_key if fm_config_manager else None,
            api_token=fm_config_manager.letsencrypt.api_token if fm_config_manager else None,
        )

    def get_certificate_expiry(self):
        """Get certificate expiry date"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.name, "Certificate manager not initialized")
        return self.certificate_manager.get_certficate_expiry()
