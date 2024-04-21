from pathlib import Path
from typing import Tuple
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.display_manager.DisplayManager import richprint


class NoOpCertificateService(SSLCertificateService):
    def __init__(self, root_dir: Path = Path('/dev/null')):
        self.root_dir = root_dir

    def renew_certificate(self):
        pass

    def remove_certificate(self, certificate: 'SSLCertificate'):
        richprint.warning(f"{certificate.domain} doesn't have certificate issued.")

    def generate_certificate(self, certificate: 'SSLCertificate') -> Tuple[Path, Path]:
        return Path('/dev/null'), Path('/dev/null')
