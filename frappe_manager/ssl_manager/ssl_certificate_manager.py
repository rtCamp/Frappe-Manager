"""
SSL Certificate Manager with multi-certificate support and dependency injection.

This module orchestrates SSL certificate operations by coordinating between:
- SSL certificate services (Let's Encrypt, acme.sh, self-signed, etc.)
- Certificate link manager (for symlink operations)
- Nginx controller (for service restarts)

The manager supports multiple certificates per bench, allowing different domains
to use different certificate types and validation methods.
"""

import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.output_manager import OutputHandler
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotDueForRenewalError,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.ssl_manager.vhost_config_manager import VhostConfigManager
from frappe_manager.utils.helpers import get_certificate_expiry_date


class SSLCertificateManager:
    """
    Manages multiple SSL certificates with dependency injection.

    This manager coordinates SSL certificate operations for multiple domains/certificates
    without being tightly coupled to specific implementations. It supports multiple
    certificates per bench and uses a service factory to create appropriate services
    for each certificate type.

    Attributes:
        certificates: List of SSL certificate configurations to manage
        service_factory: Factory function to create certificate services
        link_manager: Manages symlinks between cert files and nginx-proxy
        nginx_controller: Controls nginx service operations
        vhost_manager: Manages per-domain HTTPS redirect configuration
        storage_config: Storage configuration for SSL operations
        config_save_callback: Callback to persist config changes to bench_config.toml
        output_handler: Output handler for user-facing messages
        services: Dictionary mapping domain to certificate service instances
    """

    def __init__(
        self,
        certificates: list[SSLCertificate],
        service_factory: Callable[[SSLCertificate, SSLStorageConfig, OutputHandler], SSLCertificateService],
        link_manager: CertificateLinkManager,
        nginx_controller: NginxController,
        storage_config: SSLStorageConfig,
        output_handler: OutputHandler,
        config_save_callback: Callable[[], None] | None = None,
    ):
        """
        Initialize the SSL certificate manager.

        Args:
            certificates: List of SSL certificate configurations to manage
            service_factory: Factory function to create certificate services
            link_manager: Manager for certificate symlink operations
            nginx_controller: Controller for nginx service operations
            storage_config: Storage configuration for SSL operations
            output_handler: Output handler for user messages
            config_save_callback: Optional callback to save config after modifications

        Raises:
            ValueError: If any required dependency is None or invalid
        """
        if certificates is None:
            raise ValueError("Certificate configuration is required")
        if service_factory is None:
            raise ValueError("Certificate service factory is required")
        if link_manager is None:
            raise ValueError("Certificate link manager is required")
        if nginx_controller is None:
            raise ValueError("Nginx controller is required")
        if storage_config is None:
            raise ValueError("Storage config is required")
        if output_handler is None:
            raise ValueError("Output handler is required")

        self.certificates: list[SSLCertificate] = certificates if isinstance(certificates, list) else [certificates]
        self.service_factory = service_factory
        self.storage_config = storage_config
        self.output_handler = output_handler
        self.link_manager = link_manager
        self.nginx_controller = nginx_controller
        self.config_save_callback = config_save_callback

        # Initialize vhost config manager for per-domain HTTPS redirect control
        self.vhost_manager = VhostConfigManager(storage_config.vhostd_dir)

        # Create services for all certificates
        self.services: dict[str, SSLCertificateService] = {}
        for cert in self.certificates:
            self.services[cert.domain] = service_factory(cert, storage_config, output_handler)

    def get_primary_certificate(self) -> SSLCertificate | None:
        """
        Get the primary (first) certificate.

        Returns:
            The first certificate in the list, or None if no certificates exist
        """
        return self.certificates[0] if self.certificates else None

    def add_certificate(self, certificate: SSLCertificate, dry_run: bool = False):
        """
        Add a new certificate and issue it.

        This method:
        1. Checks if certificate already exists
        2. Creates appropriate service for the certificate
        3. Generates individual certificate (no SANs)
        4. (Dry run mode) Uses staging server, skips symlinks, nginx restart, and config save
        5. (Normal mode) Creates symlinks, restarts nginx, persists config

        Args:
            certificate: Certificate configuration to add
            dry_run: If True, uses Let's Encrypt staging server and skips system modifications

        Raises:
            ValueError: If certificate for domain already exists
            SSLCertificateGenerateFailed: If certificate generation fails
        """
        # Check if certificate already exists
        if any(cert.domain == certificate.domain for cert in self.certificates):
            raise ValueError(f"Certificate for {certificate.domain} already exists")

        # Create service for this certificate
        if self.service_factory and self.storage_config and self.output_handler:
            service = self.service_factory(certificate, self.storage_config, self.output_handler)
            self.services[certificate.domain] = service
        else:
            raise RuntimeError(
                "Cannot add certificate: service_factory, storage_config, and output_handler are required"
            )

        original_staging = None
        if dry_run:
            self.output_handler.print("[bold yellow]🧪 DRY RUN MODE: Using Let's Encrypt staging server[/bold yellow]")
            self.output_handler.print(
                "[dim]No system modifications will be made (no symlinks, nginx restart, or config save)[/dim]"
            )

            # Set staging environment variable
            original_staging = os.environ.get("FM_LETSENCRYPT_STAGING")
            os.environ["FM_LETSENCRYPT_STAGING"] = "1"

        try:
            # Generate individual certificate (no SANs)
            privkey_path, fullchain_path = service.generate_certificate(certificate)

            # Determine actual cert_type from the returned path
            ssl_dir = self.storage_config.ssl_dir
            try:
                relative_path = privkey_path.relative_to(ssl_dir)
                actual_cert_type = relative_path.parts[0]
            except (ValueError, IndexError):
                actual_cert_type = getattr(certificate, 'acme_client', 'letsencrypt')

            if dry_run:
                self.output_handler.print(
                    f"✅ [green]Certificate validated successfully for {certificate.domain}[/green]"
                )
                self.output_handler.print(f"[dim]Staging certificate: {fullchain_path}[/dim]")

                # Clean up staging certificate
                cert_dir = privkey_path.parent
                if cert_dir.exists() and cert_dir.is_relative_to(ssl_dir):
                    self.output_handler.print(f"[dim]Cleaning up staging certificate from {cert_dir}[/dim]")
                    shutil.rmtree(cert_dir, ignore_errors=True)

                self.output_handler.print("[yellow]⏭️  Skipped: Creating symlinks (dry run)[/yellow]")
                self.output_handler.print("[yellow]⏭️  Skipped: Creating vhost.d redirect config (dry run)[/yellow]")
                self.output_handler.print("[yellow]⏭️  Skipped: Restarting nginx (dry run)[/yellow]")
                self.output_handler.print("[yellow]⏭️  Skipped: Saving configuration (dry run)[/yellow]")
            else:
                # Create symlinks for nginx-proxy (individual cert, no alias_domains)
                self.link_manager.link_certificate(
                    cert_type=actual_cert_type,
                    domain=certificate.domain,
                    privkey_path=privkey_path,
                    fullchain_path=fullchain_path,
                    alias_domains=None,
                )

                # Enable HTTPS redirect for this domain now that it has a certificate
                self.vhost_manager.enable_https_redirect(certificate.domain)
                self.output_handler.print(f"Created vhost.d redirect config for {certificate.domain}")

                # Add to managed certificates
                self.certificates.append(certificate)

                # Restart nginx to pick up new certificate
                self.nginx_controller.restart()

                # Persist config if callback provided
                if self.config_save_callback:
                    self.config_save_callback()

        finally:
            if dry_run:
                # Restore original staging setting
                if original_staging is not None:
                    os.environ["FM_LETSENCRYPT_STAGING"] = original_staging
                else:
                    os.environ.pop("FM_LETSENCRYPT_STAGING", None)

    def remove_certificate_by_domain(self, domain: str):
        """
        Remove a certificate by domain name.

        This method:
        1. Finds the certificate for the domain
        2. Removes symlinks
        3. Removes actual certificate files
        4. Removes from managed certificates list
        5. Persists the config via callback

        Args:
            domain: Domain name of certificate to remove

        Raises:
            SSLCertificateNotFoundError: If no certificate exists for domain
        """
        # Find certificate
        cert_to_remove = None
        for cert in self.certificates:
            if cert.domain == domain:
                cert_to_remove = cert
                break

        if not cert_to_remove:
            raise SSLCertificateNotFoundError(domain)

        # Get service for this certificate
        service = self.services.get(domain)
        if not service:
            raise RuntimeError(f"No service found for domain {domain}")

        # Remove symlinks (individual cert, no alias_domains)
        self.link_manager.unlink_certificate(domain, alias_domains=None)

        # Disable HTTPS redirect for this domain (remove vhost.d config)
        self.vhost_manager.disable_https_redirect(domain)

        # Remove actual certificate files
        service.remove_certificate(cert_to_remove)

        # Remove from managed certificates and services
        self.certificates.remove(cert_to_remove)
        del self.services[domain]

        # Restart nginx to apply changes
        self.nginx_controller.restart()

        # Persist config if callback provided
        if self.config_save_callback:
            self.config_save_callback()

    def list_certificates(self) -> list[dict]:
        """
        List all managed certificates with their status.

        Returns:
            List of dictionaries with certificate information:
            - domain: Certificate domain
            - ssl_type: Certificate type (letsencrypt, etc.)
            - exists: Whether certificate files exist
            - expiry_date: Certificate expiry date (if exists)
            - needs_renewal: Whether certificate needs renewal
            - days_until_expiry: Days until certificate expires
        """
        result = []
        for cert in self.certificates:
            info = {
                'domain': cert.domain,
                'ssl_type': cert.ssl_type.value,
                'exists': False,
                'expiry_date': None,
                'needs_renewal': False,
                'days_until_expiry': None,
            }

            try:
                # Check if certificate exists
                privkey_path, fullchain_path = self.link_manager.get_certificate_paths(cert.domain)
                info['exists'] = True

                # Get expiry information
                expiry_date = get_certificate_expiry_date(fullchain_path)
                info['expiry_date'] = expiry_date

                # Calculate renewal status
                expiry_date_with_threshold = expiry_date - timedelta(days=SSL_RENEW_BEFORE_DAYS)
                today_date = datetime.now()
                if expiry_date_with_threshold.tzinfo:
                    today_date = today_date.replace(tzinfo=expiry_date_with_threshold.tzinfo)

                info['needs_renewal'] = not expiry_date_with_threshold > today_date

                # Calculate days until expiry
                days_until_expiry = (expiry_date - today_date).days
                info['days_until_expiry'] = days_until_expiry

            except (FileNotFoundError, SSLCertificateNotFoundError):
                pass

            result.append(info)

        return result

    def has_certificate(self, domain: str | None = None) -> bool:
        """
        Check if a certificate exists for a domain.

        Args:
            domain: Domain to check. If None, checks the primary certificate.

        Returns:
            True if certificate files exist, False otherwise
        """
        if domain is None:
            primary = self.get_primary_certificate()
            if not primary:
                return False
            domain = primary.domain

        try:
            self.link_manager.get_certificate_paths(domain)
            return True
        except (FileNotFoundError, SSLCertificateNotFoundError):
            return False

    def get_certificate_paths(self, domain: str | None = None) -> tuple[Path, Path]:
        """
        Get paths to the certificate files for a domain.

        Args:
            domain: Domain to get paths for. If None, uses primary certificate.

        Returns:
            Tuple of (privkey_path, fullchain_path)

        Raises:
            SSLCertificateNotFoundError: If certificate doesn't exist
        """
        if domain is None:
            primary = self.get_primary_certificate()
            if not primary:
                raise SSLCertificateNotFoundError("No primary certificate configured")
            domain = primary.domain

        try:
            return self.link_manager.get_certificate_paths(domain)
        except FileNotFoundError:
            raise SSLCertificateNotFoundError(domain)

    def get_certificate_expiry(self, domain: str | None = None) -> datetime:
        """
        Get the expiry date of a certificate.

        Args:
            domain: Domain to check. If None, uses primary certificate.

        Returns:
            Datetime object representing when the certificate expires

        Raises:
            SSLCertificateNotFoundError: If certificate doesn't exist
        """
        privkey_path, fullchain_path = self.get_certificate_paths(domain)
        return get_certificate_expiry_date(fullchain_path)

    def needs_renewal(self, domain: str | None = None) -> bool:
        """
        Check if a certificate needs renewal.

        A certificate needs renewal if it will expire within SSL_RENEW_BEFORE_DAYS.

        Args:
            domain: Domain to check. If None, uses primary certificate.

        Returns:
            True if certificate should be renewed, False otherwise
        """
        expiry_date = self.get_certificate_expiry(domain)
        expiry_date_with_threshold = expiry_date - timedelta(days=SSL_RENEW_BEFORE_DAYS)

        today_date = datetime.now()
        if expiry_date_with_threshold.tzinfo:
            today_date = today_date.replace(tzinfo=expiry_date_with_threshold.tzinfo)

        return not expiry_date_with_threshold > today_date

    def generate_all_certificates(self):
        """
        Generate individual SSL certificates for ALL configured certificates.

        This method generates a separate certificate for each entry in the
        certificates list, without combining domains into SAN certificates.
        Each certificate is generated independently for its own domain only.

        This is useful when you want individual certificates per domain rather
        than a single certificate covering multiple domains via SANs.

        Raises:
            SSLCertificateGenerateFailed: If any certificate generation fails
        """
        if not self.certificates:
            raise ValueError("No certificates configured")

        self.output_handler.change_head("Generating individual certificates for all domains")

        for certificate in self.certificates:
            # Get service for this certificate
            service = self.services.get(certificate.domain)
            if not service:
                raise RuntimeError(f"No service found for domain {certificate.domain}")

            # Generate certificate for this domain ONLY (individual cert)
            self.output_handler.print(f"Generating certificate for {certificate.domain}")
            privkey_path, fullchain_path = service.generate_certificate(certificate)

            # Determine actual cert_type from the returned path
            # (e.g., letsencrypt service stores in "letsencrypt", acmesh stores in "acmesh")
            ssl_dir = self.storage_config.ssl_dir
            try:
                # Path structure is: ssl_dir / cert_type / domain / file
                relative_path = privkey_path.relative_to(ssl_dir)
                actual_cert_type = relative_path.parts[0]  # First directory after ssl_dir
            except (ValueError, IndexError):
                # Fallback to certificate's ssl_type if path detection fails
                actual_cert_type = certificate.ssl_type.value

            # Create symlinks for nginx-proxy (no alias_domains)
            self.link_manager.link_certificate(
                cert_type=actual_cert_type,
                domain=certificate.domain,
                privkey_path=privkey_path,
                fullchain_path=fullchain_path,
                alias_domains=None,
            )

            # Enable HTTPS redirect for this domain
            self.vhost_manager.enable_https_redirect(certificate.domain)
            self.output_handler.print(f"Created vhost.d redirect config for {certificate.domain}")

        # Restart nginx once after all certificates are generated
        self.nginx_controller.restart()
        self.output_handler.print("All individual certificates generated successfully")

    def renew_certificate(self, domain: str | None = None, dry_run: bool = False):
        """
        Renew an existing SSL certificate.

        This renews the certificate if it's due for renewal, updates symlinks,
        and restarts nginx.

        Args:
            domain: Domain to renew. If None, uses primary certificate.
            dry_run: If True, uses Let's Encrypt staging server and skips system modifications

        Raises:
            SSLCertificateNotDueForRenewalError: If certificate doesn't need renewal yet
        """
        if domain is None:
            primary = self.get_primary_certificate()
            if not primary:
                raise ValueError("No primary certificate configured")
            certificate = primary
        else:
            # Find certificate for this domain
            certificate = None
            for cert in self.certificates:
                if cert.domain == domain:
                    certificate = cert
                    break
            if not certificate:
                raise SSLCertificateNotFoundError(domain)

        if not self.needs_renewal(certificate.domain):
            raise SSLCertificateNotDueForRenewalError(
                certificate.domain, self.get_certificate_expiry(certificate.domain)
            )

        # Get service for this certificate
        service = self.services.get(certificate.domain)
        if not service:
            raise RuntimeError(f"No service found for domain {certificate.domain}")

        original_staging = None
        if dry_run:
            self.output_handler.print("[bold yellow]🧪 DRY RUN MODE: Using Let's Encrypt staging server[/bold yellow]")
            self.output_handler.print("[dim]No system modifications will be made (no symlinks or nginx restart)[/dim]")

            # Set staging environment variable
            original_staging = os.environ.get("FM_LETSENCRYPT_STAGING")
            os.environ["FM_LETSENCRYPT_STAGING"] = "1"

        try:
            # Renew the certificate
            service.renew_certificate(certificate)

            if dry_run:
                self.output_handler.print(
                    f"✅ [green]Certificate renewal validated successfully for {certificate.domain}[/green]"
                )
                self.output_handler.print("[yellow]⏭️  Skipped: Updating symlinks (dry run)[/yellow]")
                self.output_handler.print("[yellow]⏭️  Skipped: Restarting nginx (dry run)[/yellow]")
            else:
                # Recreate symlinks for this domain
                try:
                    privkey_path, fullchain_path = self.get_certificate_paths(certificate.domain)
                    # Determine actual cert_type from path
                    ssl_dir = self.storage_config.ssl_dir
                    try:
                        relative_path = privkey_path.relative_to(ssl_dir)
                        actual_cert_type = relative_path.parts[0]
                    except (ValueError, IndexError):
                        actual_cert_type = getattr(certificate, 'acme_client', 'letsencrypt')

                    self.link_manager.link_certificate(
                        cert_type=actual_cert_type,
                        domain=certificate.domain,
                        privkey_path=privkey_path,
                        fullchain_path=fullchain_path,
                        alias_domains=None,
                    )
                except Exception:
                    # If symlink recreation fails, renewal still succeeded
                    pass

                # Restart nginx to pick up renewed certificate
                self.nginx_controller.restart()

        finally:
            if dry_run:
                # Restore original staging setting
                if original_staging is not None:
                    os.environ["FM_LETSENCRYPT_STAGING"] = original_staging
                else:
                    os.environ.pop("FM_LETSENCRYPT_STAGING", None)

    def renew_all_certificates(self, dry_run: bool = False):
        """
        Renew all SSL certificates that are due for renewal.

        This method iterates through all configured certificates and renews
        those that are due for renewal. Certificates not due for renewal are
        skipped with a warning. After all renewals, nginx is restarted once.

        Args:
            dry_run: If True, uses Let's Encrypt staging server and skips system modifications

        Raises:
            SSLCertificateNotFoundError: If any certificate doesn't exist
        """
        if not self.certificates:
            raise ValueError("No certificates configured")

        self.output_handler.change_head("Renewing certificates for all domains")

        original_staging = None
        if dry_run:
            self.output_handler.print("[bold yellow]🧪 DRY RUN MODE: Using Let's Encrypt staging server[/bold yellow]")
            self.output_handler.print("[dim]No system modifications will be made (no symlinks or nginx restart)[/dim]")

            # Set staging environment variable
            original_staging = os.environ.get("FM_LETSENCRYPT_STAGING")
            os.environ["FM_LETSENCRYPT_STAGING"] = "1"

        renewed_count = 0
        skipped_count = 0

        try:
            for certificate in self.certificates:
                try:
                    # Check if certificate needs renewal
                    if not self.needs_renewal(certificate.domain):
                        expiry_date = self.get_certificate_expiry(certificate.domain)
                        self.output_handler.print(
                            f"⏭️  Skipping {certificate.domain} (expires {expiry_date.strftime('%Y-%m-%d')})"
                        )
                        skipped_count += 1
                        continue

                    # Get service for this certificate
                    service = self.services.get(certificate.domain)
                    if not service:
                        raise RuntimeError(f"No service found for domain {certificate.domain}")

                    # Renew the certificate
                    self.output_handler.print(f"🔄 Renewing certificate for {certificate.domain}")
                    service.renew_certificate(certificate)

                    if not dry_run:
                        # Recreate symlinks for this domain
                        try:
                            privkey_path, fullchain_path = self.get_certificate_paths(certificate.domain)
                            # Determine actual cert_type from path
                            ssl_dir = self.storage_config.ssl_dir
                            try:
                                relative_path = privkey_path.relative_to(ssl_dir)
                                actual_cert_type = relative_path.parts[0]
                            except (ValueError, IndexError):
                                actual_cert_type = getattr(certificate, 'acme_client', 'letsencrypt')

                            self.link_manager.link_certificate(
                                cert_type=actual_cert_type,
                                domain=certificate.domain,
                                privkey_path=privkey_path,
                                fullchain_path=fullchain_path,
                                alias_domains=None,
                            )
                        except Exception:
                            # If symlink recreation fails, renewal still succeeded
                            pass

                    self.output_handler.print(f"✅ Successfully renewed {certificate.domain}")
                    renewed_count += 1

                except SSLCertificateNotDueForRenewalError as e:
                    self.output_handler.print(f"⏭️  {e}")
                    skipped_count += 1
                except Exception as e:
                    self.output_handler.print(f"❌ Failed to renew {certificate.domain}: {e}")

            # Restart nginx once after all renewals
            if renewed_count > 0:
                if dry_run:
                    self.output_handler.print("[yellow]⏭️  Skipped: Restarting nginx (dry run)[/yellow]")
                else:
                    self.nginx_controller.restart()
                self.output_handler.print(f"Renewal complete: {renewed_count} renewed, {skipped_count} skipped")

        finally:
            if dry_run:
                # Restore original staging setting
                if original_staging is not None:
                    os.environ["FM_LETSENCRYPT_STAGING"] = original_staging
                else:
                    os.environ.pop("FM_LETSENCRYPT_STAGING", None)

    def remove_certificate(self, domain: str | None = None):
        """
        Remove an SSL certificate and its symlinks.

        This removes the certificate files, all associated symlinks, and restarts nginx.

        Args:
            domain: Domain to remove. If None, uses primary certificate.
        """
        if domain is None:
            primary = self.get_primary_certificate()
            if not primary:
                raise ValueError("No primary certificate configured")
            certificate = primary
        else:
            # Find certificate for this domain
            certificate = None
            for cert in self.certificates:
                if cert.domain == domain:
                    certificate = cert
                    break
            if not certificate:
                raise SSLCertificateNotFoundError(domain)

        # Get service for this certificate
        service = self.services.get(certificate.domain)
        if not service:
            raise RuntimeError(f"No service found for domain {certificate.domain}")

        # Remove symlinks first
        self.link_manager.unlink_certificate(certificate.domain, None)

        # Disable HTTPS redirect for this domain (remove vhost.d config)
        self.vhost_manager.disable_https_redirect(certificate.domain)

        # Remove actual certificate files
        service.remove_certificate(certificate)

        # Restart nginx to apply changes
        self.nginx_controller.restart()
