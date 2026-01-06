"""BenchSSL - SSL Certificate Management Module

This module handles all SSL certificate operations for a bench including:
- Creating certificates
- Checking certificate existence  
- Removing certificates
- Updating certificates
- Renewing certificates
"""

from typing import TYPE_CHECKING, List

from frappe_manager.site_manager.bench_config import SSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.certificate import SUPPORTED_SSL_TYPES
from frappe_manager.site_manager.exceptions import (
    BenchSSLCertificateAlreadyIssued,
    BenchSSLCertificateNotIssued,
    BenchServiceNotRunning,
)

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench


class BenchSSL:
    """Manages SSL certificates for a bench."""

    def __init__(
        self,
        certificate_manager: SSLCertificateManager,
        bench_name: str,
        is_service_running_fn,
    ):
        """
        Initialize BenchSSL module.

        Args:
            certificate_manager: SSL certificate manager instance
            bench_name: Name of the bench
            is_service_running_fn: Function to check if a service is running
        """
        self.certificate_manager = certificate_manager
        self.bench_name = bench_name
        self._is_service_running = is_service_running_fn

    def create_certificate(self, alias_domains: List[str]) -> None:
        """
        Create SSL certificate for the bench.

        Args:
            alias_domains: List of alias domains for the certificate
        """
        self.certificate_manager.generate_certificate(alias_domains)

    def has_certificate(self) -> bool:
        """
        Check if bench has an SSL certificate.

        Returns:
            True if certificate exists, False otherwise
        """
        return self.certificate_manager.has_certificate()

    def remove_certificate(self, alias_domains: List[str]) -> None:
        """
        Remove SSL certificate from the bench.

        Args:
            alias_domains: List of alias domains to remove from certificate
        """
        self.certificate_manager.remove_certificate(alias_domains)

    def update_certificate(
        self, certificate: SSLCertificate, alias_domains: List[str], raise_error: bool = True
    ) -> bool:
        """
        Update SSL certificate configuration.

        Args:
            certificate: New certificate configuration
            alias_domains: List of alias domains for the certificate
            raise_error: Whether to raise error on failures

        Returns:
            True if update was successful

        Raises:
            BenchSSLCertificateAlreadyIssued: If certificate already exists (when raise_error=True)
            BenchSSLCertificateNotIssued: If trying to remove non-existent cert (when raise_error=True)
        """
        if certificate.ssl_type == SUPPORTED_SSL_TYPES.le:
            if self.has_certificate():
                if raise_error:
                    raise BenchSSLCertificateAlreadyIssued(self.bench_name)
            else:
                self.certificate_manager.set_certificate(certificate)
                self.create_certificate(alias_domains)

        elif certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            if self.has_certificate():
                self.remove_certificate(alias_domains)
            else:
                if not raise_error:
                    return True
                raise BenchSSLCertificateNotIssued(self.bench_name)

        return True

    def renew_certificate(self, alias_domains: List[str]) -> None:
        """
        Renew existing SSL certificate.

        Args:
            alias_domains: List of alias domains for the certificate

        Raises:
            BenchSSLCertificateNotIssued: If no certificate exists
            BenchServiceNotRunning: If nginx service is not running
        """
        if not self.has_certificate():
            raise BenchSSLCertificateNotIssued(self.bench_name)

        if not self._is_service_running('nginx'):
            raise BenchServiceNotRunning(self.bench_name, 'nginx')

        self.certificate_manager.renew_certificate(alias_domains)
