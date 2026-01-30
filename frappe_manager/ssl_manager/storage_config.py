"""
Configuration for SSL certificate storage locations.

This module provides a dataclass that encapsulates all path-related configuration
for SSL certificate storage, decoupling the certificate management logic from
infrastructure-specific directory structures.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SSLStorageConfig:
    """
    Configuration for SSL certificate storage locations.

    This class holds all path information needed for certificate management,
    separating configuration from business logic and making the system more testable.

    Attributes:
        ssl_dir: Root directory where ACME client stores certificates (host filesystem)
        ssl_dir_container: Root directory where ACME client stores certificates (container filesystem)
        certs_dir: Directory where nginx-proxy looks for certificates (host filesystem)
        certs_dir_container: Directory where nginx-proxy looks for certificates (container filesystem)
        vhostd_dir: Directory for nginx-proxy vhost.d configuration files (host filesystem)
        webroot_dir: Directory for HTTP-01 challenge files (host filesystem)
        validate_on_init: Whether to validate paths exist during initialization (default: False)
    """

    ssl_dir: Path
    """Root directory where ACME client stores certificates (host filesystem)"""

    ssl_dir_container: Path
    """Root directory where ACME client stores certificates (container filesystem)"""

    certs_dir: Path
    """Directory where nginx-proxy looks for certificates (host filesystem)"""

    certs_dir_container: Path
    """Directory where nginx-proxy looks for certificates (container filesystem)"""

    vhostd_dir: Path
    """Directory for nginx-proxy vhost.d configuration files (host filesystem)"""

    webroot_dir: Path
    """Directory for HTTP-01 challenge files (host filesystem)"""

    validate_on_init: bool = False
    """Whether to validate paths exist during initialization"""

    def __post_init__(self):
        """Validate paths if requested."""
        if self.validate_on_init:
            self.validate()

    def validate(self) -> None:
        """
        Validate that the configuration is valid.

        Raises:
            ValueError: If any required path is invalid or doesn't exist
        """
        if not self.ssl_dir.exists():
            raise ValueError(f"SSL root directory does not exist: {self.ssl_dir}")

        if not self.certs_dir.exists():
            raise ValueError(f"Certificates directory does not exist: {self.certs_dir}")

        if not self.vhostd_dir.exists():
            raise ValueError(f"vhost.d directory does not exist: {self.vhostd_dir}")

        if not self.webroot_dir.exists():
            raise ValueError(f"Webroot directory does not exist: {self.webroot_dir}")
