from pathlib import Path
from typing import Optional, Any
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import BaseSSLConfig
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.site_manager.site_exceptions import SiteCertificateException
from frappe_manager.display_manager.DisplayManager import richprint

class BenchSSLManager:
    def __init__(self, site_name: str, config: dict):
        self.site_name = site_name
        self.config = config
        self.certificate: Optional[BaseSSLConfig] = None
        self.certificate_manager: Optional[SSLCertificateManager] = None

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
            raise SiteCertificateException(self.site_name, "Certificate manager not initialized")
        self.certificate_manager.generate_certificate()
        self._save_certificate_config()

    def has_certificate(self) -> bool:
        """Check if site has SSL certificate"""
        return self.certificate_manager.has_certificate() if self.certificate_manager else False

    def remove_certificate(self):
        """Remove SSL certificate from the site"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.site_name, "Certificate manager not initialized")
        self.certificate_manager.remove_certificate()
        self.certificate = BaseSSLConfig(domain=self.site_name, ssl_type=SUPPORTED_SSL_TYPES.none)
        self._save_certificate_config()

    def update_certificate(self, certificate: BaseSSLConfig, raise_error: bool = True) -> bool:
        """Update site's SSL certificate"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.site_name, "Certificate manager not initialized")

        if certificate.ssl_type == SUPPORTED_SSL_TYPES.le:
            if self.has_certificate():
                if raise_error:
                    raise SiteCertificateException(self.site_name, "Certificate already issued")
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
                raise SiteCertificateException(self.site_name, "No certificate issued")

        return True

    def renew_certificate(self):
        """Renew site's SSL certificate"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.site_name, "Certificate manager not initialized")
        if not self.has_certificate():
            raise SiteCertificateException(self.site_name, "No certificate issued")
        self.certificate_manager.renew_certificate()

    def _save_certificate_config(self):
        """Save certificate configuration to site config dict"""
        self.config['ssl'] = self.certificate.model_dump() if self.certificate else None

    def get_certificate_expiry(self):
        """Get certificate expiry date"""
        if not self.certificate_manager:
            raise SiteCertificateException(self.site_name, "Certificate manager not initialized")
        return self.certificate_manager.get_certficate_expiry()
