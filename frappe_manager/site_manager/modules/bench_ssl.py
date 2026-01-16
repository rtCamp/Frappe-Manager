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

    def create_individual_certificates(self) -> None:
        """
        Create individual SSL certificates for all domains.

        This generates separate certificates for the primary domain and each
        alias domain, rather than a single SAN certificate covering all domains.
        """
        self.certificate_manager.generate_all_certificates()

    def has_certificate(self) -> bool:
        """
        Check if bench has an SSL certificate.

        Returns:
            True if certificate exists, False otherwise
        """
        return self.certificate_manager.has_certificate()

    def remove_certificate(self, domain: str | None = None) -> None:
        """
        Remove SSL certificate from the bench.

        Args:
            domain: Domain to remove certificate for. If None, removes primary certificate.
        """
        self.certificate_manager.remove_certificate(domain)

    def remove_all_certificates(self) -> None:
        """
        Remove ALL SSL certificates from the bench.

        This removes all certificates for the primary domain and all alias domains,
        including their symlinks, vhost configs, and acme.sh configurations.
        This is useful for complete cleanup when deleting a bench.
        """
        self.certificate_manager.remove_all_certificates()

    def update_certificate(self, certificate: SSLCertificate, raise_error: bool = True) -> bool:
        """
        Update SSL certificate configuration.

        Args:
            certificate: New certificate configuration
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
                    return False
            else:
                self.create_individual_certificates()

        elif certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
            if self.has_certificate():
                self.remove_certificate()
            else:
                if not raise_error:
                    return False
                raise BenchSSLCertificateNotIssued(self.bench_name)

        return True

    def renew_certificate(self, domain: str | None = None, dry_run: bool = False) -> None:
        """
        Renew existing SSL certificate.

        Args:
            domain: Domain to renew certificate for. If None, renews primary certificate.
            dry_run: If True, uses Let's Encrypt staging server and skips system modifications

        Raises:
            BenchSSLCertificateNotIssued: If no certificate exists
            BenchServiceNotRunning: If nginx service is not running
        """
        if not self.has_certificate():
            raise BenchSSLCertificateNotIssued(self.bench_name)

        if not self._is_service_running('nginx'):
            raise BenchServiceNotRunning(self.bench_name, 'nginx')

        self.certificate_manager.renew_certificate(domain, dry_run=dry_run)

    def renew_all_certificates(self, dry_run: bool = False) -> None:
        """
        Renew all SSL certificates for the bench.

        This renews all certificates that are due for renewal.
        Certificates not due for renewal are skipped.

        Args:
            dry_run: If True, uses Let's Encrypt staging server and skips system modifications

        Raises:
            BenchSSLCertificateNotIssued: If no certificates exist
            BenchServiceNotRunning: If nginx service is not running
        """
        if not self.has_certificate():
            raise BenchSSLCertificateNotIssued(self.bench_name)

        if not self._is_service_running('nginx'):
            raise BenchServiceNotRunning(self.bench_name, 'nginx')

        self.certificate_manager.renew_all_certificates(dry_run=dry_run)
