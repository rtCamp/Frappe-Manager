from pathlib import Path

from frappe_manager.ssl_manager.certificate import RenewableSSLCertificate, SSLCertificate
from frappe_manager.ssl_manager.renewable_certificate import RenewableLetsencryptSSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.display_manager.DisplayManager import richprint


class NoOpCertificateService(SSLCertificateService):
    def __init__(self, root_dir: Path = Path('/dev/null')):
        self.root_dir = root_dir
        pass

    def is_certificate_exists(self, certificate: SSLCertificate) -> RenewableSSLCertificate:
        ...

    def reload_proxy(self):
        ...

    def needs_renewal(self, certificate: RenewableSSLCertificate) -> bool:
        return False

    def renew_certificate(self):
        ...

    def remove_certificate(self, certificate: 'RenewableSSLCertificate') -> bool:
        richprint.warning(f"{certificate.domain} doesn't have certificate issued. Skipping certficate removal.")

    def generate_certificate(self, certificate: 'SSLCertificate') -> RenewableSSLCertificate:
        cert = RenewableSSLCertificate(
            domain=certificate.domain,
            ssl_type=certificate.ssl_type,
            privkey_path=Path('/dev/null'),
            fullchain_path=Path('/dev/null'),
            proxy_certs_dir=Path('/dev/null'),
            ssl_service_container_dir=Path('/dev/null'),
            )
        return cert
