from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from frappe_manager.ssl_manager.certificate import SSLCertificate


@runtime_checkable
class SSLCertificateService(Protocol):
    """
    Protocol for SSL certificate service implementations.

    All certificate services generate individual certificates (one domain per certificate).
    SAN certificates are deprecated.
    """

    root_dir: Path

    def renew_certificate(self, certificate: "SSLCertificate") -> bool: ...

    def remove_certificate(self, certificate: "SSLCertificate") -> bool: ...

    def generate_certificate(self, certificate: "SSLCertificate") -> tuple[Path, Path]:
        """Generate individual certificate for a single domain (no SANs)."""
        ...
