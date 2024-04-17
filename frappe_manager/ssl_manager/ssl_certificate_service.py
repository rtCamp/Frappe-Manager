from pathlib import Path
from typing import runtime_checkable, Protocol, TYPE_CHECKING
if TYPE_CHECKING:
    from frappe_manager.ssl_manager.certificate import RenewableSSLCertificate
    from frappe_manager.ssl_manager.certificate import SSLCertificate

@runtime_checkable
class SSLCertificateService(Protocol):
    root_dir: Path

    def is_certificate_exists(self, certificate: 'SSLCertificate') -> 'RenewableSSLCertificate':
        ...

    def reload_proxy(self):
        ...

    def needs_renewal(self, certificate: 'RenewableSSLCertificate') -> bool:
        ...

    def renew_certificate(self, certificate: 'RenewableSSLCertificate') -> bool:
        ...

    def remove_certificate(self, certificate: 'RenewableSSLCertificate') -> bool:
        ...

    def generate_certificate(self, certificate: 'SSLCertificate') -> 'RenewableSSLCertificate':
        ...


