"""
SSL Certificate Manager with dependency injection.

This module orchestrates SSL certificate operations by coordinating between:
- SSL certificate services (Let's Encrypt, self-signed, etc.)
- Certificate link manager (for symlink operations)
- Nginx controller (for service restarts)

The manager is designed to be testable and decoupled from infrastructure details.
"""

from datetime import timedelta, datetime
from typing import Optional, List
from pathlib import Path
from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotDueForRenewalError,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.utils.helpers import get_certificate_expiry_date


class SSLCertificateManager:
    """
    Manages SSL certificate lifecycle with dependency injection.
    
    This manager coordinates SSL certificate operations without being tightly
    coupled to specific implementations. Dependencies are injected, making the
    class testable and flexible.
    
    Attributes:
        certificate: The SSL certificate configuration to manage
        service: The certificate service (Let's Encrypt, self-signed, etc.)
        link_manager: Manages symlinks between cert files and nginx-proxy
        nginx_controller: Controls nginx service operations
    """
    
    def __init__(
        self,
        certificate: SSLCertificate,
        service: SSLCertificateService,
        link_manager: CertificateLinkManager,
        nginx_controller: NginxController,
    ):
        """
        Initialize the SSL certificate manager.
        
        Args:
            certificate: SSL certificate configuration
            service: Certificate service for generation/renewal operations
            link_manager: Manager for certificate symlink operations
            nginx_controller: Controller for nginx service operations
            
        Raises:
            ValueError: If any required dependency is None
        """
        if certificate is None:
            raise ValueError("Certificate configuration is required")
        if service is None:
            raise ValueError("Certificate service is required")
        if link_manager is None:
            raise ValueError("Certificate link manager is required")
        if nginx_controller is None:
            raise ValueError("Nginx controller is required")
        
        self.certificate = certificate
        self.service = service
        self.link_manager = link_manager
        self.nginx_controller = nginx_controller
    
    def set_certificate(self, certificate: SSLCertificate):
        """
        Update the certificate configuration.
        
        Args:
            certificate: New certificate configuration
        """
        self.certificate = certificate
    
    def has_certificate(self) -> bool:
        """
        Check if a certificate exists for the configured domain.
        
        Returns:
            True if certificate files exist, False otherwise
        """
        try:
            self.get_certificate_paths()
            return True
        except SSLCertificateNotFoundError:
            return False
    
    def get_certificate_paths(self) -> tuple[Path, Path]:
        """
        Get paths to the certificate files.
        
        Returns:
            Tuple of (privkey_path, fullchain_path)
            
        Raises:
            SSLCertificateNotFoundError: If certificate doesn't exist
        """
        try:
            return self.link_manager.get_certificate_paths(self.certificate.domain)
        except FileNotFoundError:
            raise SSLCertificateNotFoundError(self.certificate.domain)
    
    def get_certificate_expiry(self) -> datetime:
        """
        Get the expiry date of the certificate.
        
        Returns:
            Datetime object representing when the certificate expires
            
        Raises:
            SSLCertificateNotFoundError: If certificate doesn't exist
        """
        privkey_path, fullchain_path = self.get_certificate_paths()
        return get_certificate_expiry_date(fullchain_path)
    
    def needs_renewal(self) -> bool:
        """
        Check if the certificate needs renewal.
        
        A certificate needs renewal if it will expire within SSL_RENEW_BEFORE_DAYS.
        
        Returns:
            True if certificate should be renewed, False otherwise
        """
        expiry_date = self.get_certificate_expiry()
        expiry_date_with_threshold = expiry_date - timedelta(days=SSL_RENEW_BEFORE_DAYS)
        
        today_date = datetime.now()
        if expiry_date_with_threshold.tzinfo:
            today_date = today_date.replace(tzinfo=expiry_date_with_threshold.tzinfo)
        
        return not expiry_date_with_threshold > today_date
    
    def generate_certificate(self, alias_domains: Optional[List[str]] = None):
        """
        Generate a new SSL certificate.
        
        This creates a new certificate, sets up symlinks for the domain and any
        alias domains, and restarts nginx to apply the changes.
        
        Args:
            alias_domains: Optional list of alias domains to include in certificate
        """
        # Generate certificate files
        privkey_path, fullchain_path = self.service.generate_certificate(
            self.certificate, alias_domains
        )
        
        # Create symlinks for nginx-proxy
        self.link_manager.link_certificate(
            cert_type=self.certificate.ssl_type.value,
            domain=self.certificate.domain,
            privkey_path=privkey_path,
            fullchain_path=fullchain_path,
            alias_domains=alias_domains,
        )
        
        # Restart nginx to pick up new certificate
        self.nginx_controller.restart()
    
    def renew_certificate(self, alias_domains: Optional[List[str]] = None):
        """
        Renew an existing SSL certificate.
        
        This renews the certificate if it's due for renewal, updates symlinks
        to handle any new alias domains, and restarts nginx.
        
        Args:
            alias_domains: Optional list of alias domains (may have changed since generation)
            
        Raises:
            SSLCertificateNotDueForRenewalError: If certificate doesn't need renewal yet
        """
        if not self.needs_renewal():
            raise SSLCertificateNotDueForRenewalError(
                self.certificate.domain, self.get_certificate_expiry()
            )
        
        # Renew the certificate
        self.service.renew_certificate(self.certificate)
        
        # Recreate symlinks to ensure alias domains are covered
        # This handles cases where alias domains were added after initial generation
        try:
            privkey_path, fullchain_path = self.get_certificate_paths()
            self.link_manager.link_certificate(
                cert_type=self.certificate.ssl_type.value,
                domain=self.certificate.domain,
                privkey_path=privkey_path,
                fullchain_path=fullchain_path,
                alias_domains=alias_domains,
            )
        except Exception:
            # If symlink recreation fails, renewal still succeeded
            pass
        
        # Restart nginx to pick up renewed certificate
        self.nginx_controller.restart()
    
    def remove_certificate(self, alias_domains: Optional[List[str]] = None):
        """
        Remove an SSL certificate and its symlinks.
        
        This removes the certificate files, all associated symlinks (including
        alias domains), and restarts nginx.
        
        Args:
            alias_domains: Optional list of alias domains to also remove symlinks for
        """
        # Remove symlinks first
        self.link_manager.unlink_certificate(self.certificate.domain, alias_domains)
        
        # Remove actual certificate files
        self.service.remove_certificate(self.certificate)
        
        # Restart nginx to apply changes
        self.nginx_controller.restart()
