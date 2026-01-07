from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from frappe_manager.ssl_manager.certificate import SSLCertificate


@runtime_checkable
class SSLCertificateService(Protocol):
    root_dir: Path

    def renew_certificate(self, certificate: "SSLCertificate") -> bool: ...

    def remove_certificate(self, certificate: "SSLCertificate") -> bool: ...

    def generate_certificate(
        self, certificate: "SSLCertificate", alias_domains: list[str] | None = None,
    ) -> tuple[Path, Path]: ...
