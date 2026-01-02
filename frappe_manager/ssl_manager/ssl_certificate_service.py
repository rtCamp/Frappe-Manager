from pathlib import Path
from typing import Tuple, runtime_checkable, Protocol, TYPE_CHECKING, Optional, List

if TYPE_CHECKING:
    from frappe_manager.ssl_manager.certificate import SSLCertificate


@runtime_checkable
class SSLCertificateService(Protocol):
    root_dir: Path

    def renew_certificate(self, certificate: 'SSLCertificate') -> bool: ...

    def remove_certificate(self, certificate: 'SSLCertificate') -> bool: ...

    def generate_certificate(self, certificate: 'SSLCertificate', alias_domains: Optional[List[str]]=None) -> Tuple[Path, Path]: ...
