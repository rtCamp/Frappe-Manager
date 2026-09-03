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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateManualRenewalRequired,
    SSLCertificateNotDueForRenewalError,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager.certificate_link_manager import CertificateLinkManager
from frappe_manager.ssl_manager.nginx_controller import NginxController
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig
from frappe_manager.ssl_manager.vhost_config_manager import VhostConfigManager
from frappe_manager.utils.helpers import get_certificate_expiry_date


@contextmanager
def _letsencrypt_staging(enabled: bool) -> Iterator[None]:
    """Point acme.sh at the Let's Encrypt staging CA for the block, then restore.

    ``FM_LETSENCRYPT_STAGING`` is read by the acme.sh service to select the staging
    directory. Restoring the prior value (or unsetting it) is the point of this
    helper: leaking staging mode into a real issuance would mint untrusted
    certificates. No-op when ``enabled`` is False.
    """
    if not enabled:
        yield
        return

    original_staging = os.environ.get("FM_LETSENCRYPT_STAGING")
    os.environ["FM_LETSENCRYPT_STAGING"] = "1"
    try:
        yield
    finally:
        if original_staging is not None:
            os.environ["FM_LETSENCRYPT_STAGING"] = original_staging
        else:
            os.environ.pop("FM_LETSENCRYPT_STAGING", None)


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

        self.logger = get_logger(component="ssl_manager")
        self.certificates: list[SSLCertificate] = certificates if isinstance(certificates, list) else [certificates]
        self.service_factory = service_factory
        self.storage_config = storage_config
        self.output_handler = output_handler
        self.link_manager = link_manager
        self.nginx_controller = nginx_controller
        self.config_save_callback = config_save_callback

        self.vhost_manager = VhostConfigManager(storage_config.vhostd_dir)

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
        self.logger.info("Adding certificate", extra_fields={"domain": certificate.domain, "dry_run": dry_run})

        if any(cert.domain == certificate.domain for cert in self.certificates):
            self.logger.warning("Certificate already exists", extra_fields={"domain": certificate.domain})
            raise ValueError(f"Certificate for {certificate.domain} already exists")

        if self.service_factory and self.storage_config and self.output_handler:
            service = self.service_factory(certificate, self.storage_config, self.output_handler)
            self.services[certificate.domain] = service
            self.logger.debug("Created certificate service", extra_fields={"domain": certificate.domain})
        else:
            self.logger.error("Cannot add certificate: missing dependencies")
            raise RuntimeError(
                "Cannot add certificate: service_factory, storage_config, and output_handler are required",
            )

        if dry_run:
            self.output_handler.print(
                "[fm.warn]DRY RUN MODE: Using Let's Encrypt staging server[/fm.warn]",
                emoji_code="🧪",
            )
            self.output_handler.print(
                "[fm.muted]No system modifications will be made (no symlinks, nginx restart, or config save)[/fm.muted]",
            )
            self.logger.debug("Enabled staging mode for dry run")

        with _letsencrypt_staging(dry_run):
            self.logger.info("Generating certificate", extra_fields={"domain": certificate.domain})
            privkey_path, fullchain_path = service.generate_certificate(certificate, dry_run=dry_run)
            self.logger.info(
                "Certificate generated",
                extra_fields={
                    "domain": certificate.domain,
                    "privkey": str(privkey_path),
                    "fullchain": str(fullchain_path),
                },
            )

            ssl_dir = self.storage_config.ssl_dir

            try:
                relative_path = privkey_path.relative_to(ssl_dir)
                actual_cert_type = relative_path.parts[0]
            except (ValueError, IndexError):
                actual_cert_type = getattr(certificate, "acme_client", "letsencrypt")

            if dry_run:
                self.output_handler.print(f"[fm.ok]Certificate validated successfully for {certificate.domain}[/fm.ok]")
                self.output_handler.print("[fm.warn]Skipped: Creating symlinks (dry run)[/fm.warn]", emoji_code="⏭️ ")
                self.output_handler.print(
                    "[fm.warn]Skipped: Creating vhost.d redirect config (dry run)[/fm.warn]",
                    emoji_code="⏭️ ",
                )
                self.output_handler.print("[fm.warn]Skipped: Restarting nginx (dry run)[/fm.warn]", emoji_code="⏭️ ")
                self.output_handler.print("[fm.warn]Skipped: Saving configuration (dry run)[/fm.warn]", emoji_code="⏭️ ")
                self.logger.info("Dry run completed successfully", extra_fields={"domain": certificate.domain})
            else:
                self.logger.info(
                    "Creating certificate symlinks",
                    extra_fields={"domain": certificate.domain, "cert_type": actual_cert_type},
                )
                self.link_manager.link_certificate(
                    cert_type=actual_cert_type,
                    domain=certificate.domain,
                    privkey_path=privkey_path,
                    fullchain_path=fullchain_path,
                    alias_domains=None,
                )

                self.vhost_manager.enable_https_redirect(certificate.domain)
                self.output_handler.print(f"Created vhost.d redirect config for {certificate.domain}")
                self.logger.debug("Enabled HTTPS redirect", extra_fields={"domain": certificate.domain})

                self.certificates.append(certificate)
                self.logger.info("Restarting nginx")
                self.nginx_controller.restart()
                if self.config_save_callback:
                    self.config_save_callback()
                    self.logger.debug("Saved configuration")

                self.logger.info("Certificate added successfully", extra_fields={"domain": certificate.domain})

    def remove_certificate_by_domain(self, domain: str):
        self.logger.info("Removing certificate", extra_fields={"domain": domain})

        cert_to_remove = None
        for cert in self.certificates:
            if cert.domain == domain:
                cert_to_remove = cert
                break

        if not cert_to_remove:
            self.logger.warning("Certificate not found", extra_fields={"domain": domain})
            raise SSLCertificateNotFoundError(domain)

        service = self.services.get(domain)
        if not service:
            self.logger.error("No service found for domain", extra_fields={"domain": domain})
            raise RuntimeError(f"No service found for domain {domain}")

        self.logger.debug("Unlinking certificate symlinks", extra_fields={"domain": domain})
        self.link_manager.unlink_certificate(domain, alias_domains=None)

        self.vhost_manager.disable_https_redirect(domain)
        self.logger.debug("Disabled HTTPS redirect", extra_fields={"domain": domain})

        self.logger.debug("Removing certificate files", extra_fields={"domain": domain})
        service.remove_certificate(cert_to_remove)

        self.certificates.remove(cert_to_remove)
        del self.services[domain]

        self.logger.info("Restarting nginx")
        self.nginx_controller.restart()

        if self.config_save_callback:
            self.config_save_callback()
            self.logger.debug("Saved configuration")

        self.logger.info("Certificate removed successfully", extra_fields={"domain": domain})

    def list_certificates(self) -> list[dict]:
        """
        List all managed certificates with their status.

        Returns:
            List of dictionaries with certificate information:
            - domain: Certificate domain
            - ssl_type: Certificate type (letsencrypt, etc.)
            - challenge_type: Challenge type (http01 or dns01)
            - exists: Whether certificate files exist
            - expiry_date: Certificate expiry date (if exists)
            - needs_renewal: Whether certificate needs renewal
            - days_until_expiry: Days until certificate expires
        """
        result = []
        for cert in self.certificates:
            info = {
                "domain": cert.domain,
                "ssl_type": cert.ssl_type.value,
                "challenge_type": cert.challenge_type.value if cert.challenge_type else None,
                "exists": False,
                "expiry_date": None,
                "needs_renewal": False,
                "days_until_expiry": None,
            }

            try:
                # Check if certificate exists
                privkey_path, fullchain_path = self.link_manager.get_certificate_paths(cert.domain)
                info["exists"] = True

                # Get expiry information
                expiry_date = get_certificate_expiry_date(fullchain_path)
                info["expiry_date"] = expiry_date

                # Calculate renewal status
                expiry_date_with_threshold = expiry_date - timedelta(days=SSL_RENEW_BEFORE_DAYS)
                today_date = datetime.now()
                if expiry_date_with_threshold.tzinfo:
                    today_date = today_date.replace(tzinfo=expiry_date_with_threshold.tzinfo)

                info["needs_renewal"] = not expiry_date_with_threshold > today_date

                # Calculate days until expiry
                days_until_expiry = (expiry_date - today_date).days
                info["days_until_expiry"] = days_until_expiry

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

    def _renew_single_certificate(
        self,
        certificate: SSLCertificate,
        dry_run: bool,
        force: bool,
        skip_nginx_restart: bool = False,
    ):
        """
        Core renewal logic for a single certificate.

        This method handles the actual renewal process including fallback to re-issue
        if the certificate is not found in acme.sh.

        Args:
            certificate: The certificate to renew
            dry_run: If True, uses Let's Encrypt staging server and skips system modifications
            force: If True, forces renewal even if certificate is not due for renewal
            skip_nginx_restart: If True, skips nginx restart (for batch renewals)

        Raises:
            SSLCertificateNotDueForRenewalError: If certificate doesn't need renewal yet
            RuntimeError: If no service found for the domain
        """
        if not force and not self.needs_renewal(certificate.domain):
            raise SSLCertificateNotDueForRenewalError(
                certificate.domain,
                self.get_certificate_expiry(certificate.domain),
            )

        # Get service for this certificate
        service = self.services.get(certificate.domain)
        if not service:
            raise RuntimeError(f"No service found for domain {certificate.domain}")

        # Renew the certificate
        self.output_handler.print(f"Renewing certificate for {certificate.domain}", emoji_code="🔄")
        renewal_success = service.renew_certificate(certificate, dry_run=dry_run)

        reissued_paths = None
        if not renewal_success:
            self.output_handler.print(
                "[fm.warn]Certificate not found in acme.sh, re-issuing...[/fm.warn]",
                emoji_code="⚠️",
            )
            key_path, fullchain_path = service.generate_certificate(certificate, dry_run=dry_run)
            reissued_paths = (key_path, fullchain_path)

            if not dry_run:
                self.output_handler.print(f"[fm.ok]Certificate re-issued successfully for {certificate.domain}[/fm.ok]")

        if dry_run:
            self.output_handler.print(
                f"[fm.ok]Certificate renewal validated successfully for {certificate.domain}[/fm.ok]",
            )
            self.output_handler.print("[fm.warn]️Skipped: Updating symlinks (dry run)[/fm.warn]", emoji_code="⏭ ")
            if not skip_nginx_restart:
                self.output_handler.print("[fm.warn]Skipped: Restarting nginx (dry run)[/fm.warn]", emoji_code="⏭️ ")
        else:
            try:
                if reissued_paths:
                    privkey_path, fullchain_path = reissued_paths
                else:
                    privkey_path, fullchain_path = self.get_certificate_paths(certificate.domain)

                ssl_dir = self.storage_config.ssl_dir
                try:
                    relative_path = privkey_path.relative_to(ssl_dir)
                    actual_cert_type = relative_path.parts[0]
                except (ValueError, IndexError):
                    actual_cert_type = getattr(certificate, "acme_client", "letsencrypt")

                self.link_manager.link_certificate(
                    cert_type=actual_cert_type,
                    domain=certificate.domain,
                    privkey_path=privkey_path,
                    fullchain_path=fullchain_path,
                    alias_domains=None,
                )
            except Exception:
                pass

            if not skip_nginx_restart:
                self.nginx_controller.restart()

            self.output_handler.print(f"Successfully renewed {certificate.domain}")

    def renew_certificate(self, domain: str | None = None, dry_run: bool = False, force: bool = False):
        self.logger.info(
            "Renewing certificate",
            extra_fields={"domain": domain or "primary", "dry_run": dry_run, "force": force},
        )

        if domain is None:
            primary = self.get_primary_certificate()
            if not primary:
                self.logger.error("No primary certificate configured")
                raise ValueError("No primary certificate configured")
            certificate = primary
        else:
            certificate = None
            for cert in self.certificates:
                if cert.domain == domain:
                    certificate = cert
                    break
            if not certificate:
                self.logger.warning("Certificate not found for renewal", extra_fields={"domain": domain})
                raise SSLCertificateNotFoundError(domain)

        if dry_run:
            self.output_handler.print(
                "[fm.warn]DRY RUN MODE: Using Let's Encrypt staging server[/fm.warn]",
                emoji_code="🧪 ",
            )
            self.output_handler.print(
                "[fm.muted]No system modifications will be made (no symlinks or nginx restart)[/fm.muted]"
            )
            self.logger.debug("Enabled staging mode for dry run")

        with _letsencrypt_staging(dry_run):
            self._renew_single_certificate(
                certificate=certificate,
                dry_run=dry_run,
                force=force,
                skip_nginx_restart=False,
            )
            self.logger.info("Certificate renewed successfully", extra_fields={"domain": certificate.domain})

    def renew_all_certificates(self, dry_run: bool = False, force: bool = False):
        """
        Renew all SSL certificates that are due for renewal.

        This method iterates through all configured certificates and renews
        those that are due for renewal. Certificates not due for renewal are
        skipped with a warning. After all renewals, nginx is restarted once.

        Args:
            dry_run: If True, uses Let's Encrypt staging server and skips system modifications
            force: If True, forces renewal for all certificates regardless of expiry

        Raises:
            SSLCertificateNotFoundError: If any certificate doesn't exist
        """
        if not self.certificates:
            raise ValueError("No certificates configured")

        self.output_handler.change_head("Renewing certificates for all domains")

        if dry_run:
            self.output_handler.print(
                "[fm.warn]DRY RUN MODE: Using Let's Encrypt staging server[/fm.warn]",
                emoji_code="🧪",
            )
            self.output_handler.print(
                "[fm.muted]No system modifications will be made (no symlinks or nginx restart)[/fm.muted]"
            )

        renewed_count = 0
        skipped_count = 0

        with _letsencrypt_staging(dry_run):
            for certificate in self.certificates:
                try:
                    self._renew_single_certificate(
                        certificate=certificate,
                        dry_run=dry_run,
                        force=force,
                        skip_nginx_restart=True,
                    )
                    renewed_count += 1

                except SSLCertificateNotDueForRenewalError as e:
                    self.output_handler.print(f"{e}", emoji_code="⏭️ ")
                    skipped_count += 1
                except SSLCertificateManualRenewalRequired as e:
                    # Working as designed, not a failure: fm never auto-renews this type, so this
                    # is the same "nothing to do here" outcome as not-due-yet, just for a
                    # different reason. The message already names the exact command to run.
                    self.output_handler.print(f"{e}", emoji_code="⏭️ ")
                    skipped_count += 1
                except Exception as e:
                    self.output_handler.print(f"Failed to renew {certificate.domain}: {e}", emoji_code="❌")

            if renewed_count > 0:
                if dry_run:
                    self.output_handler.print("[fm.warn]Skipped: Restarting nginx (dry run)[/fm.warn]", emoji_code="⏭️ ")
                else:
                    self.nginx_controller.restart()
                self.output_handler.print(f"Renewal complete: {renewed_count} renewed, {skipped_count} skipped")

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

    def remove_all_certificates(self):
        """
        Remove ALL SSL certificates managed by this manager.

        This removes all certificate files, symlinks, vhost configs, and acme.sh
        configurations for every certificate in the certificates list. This is
        useful for cleanup when deleting a bench entirely.

        The nginx service is restarted once after all certificates are removed.
        """
        if not self.certificates:
            self.output_handler.print("No certificates to remove")
            return

        self.output_handler.change_head(f"Removing all SSL certificates ({len(self.certificates)} total)")

        removed_count = 0
        failed_domains = []

        for certificate in self.certificates[:]:  # Create a copy to iterate over
            try:
                self.output_handler.print(f"Removing certificate for {certificate.domain}")

                # Get service for this certificate
                service = self.services.get(certificate.domain)
                if not service:
                    self.output_handler.warning(f"No service found for domain {certificate.domain}, skipping")
                    continue

                # Remove symlinks
                try:
                    self.link_manager.unlink_certificate(certificate.domain, None)
                except Exception as e:
                    self.output_handler.warning(f"Failed to remove symlinks for {certificate.domain}: {e}")

                # Disable HTTPS redirect for this domain (remove vhost.d config)
                try:
                    self.vhost_manager.disable_https_redirect(certificate.domain)
                except Exception as e:
                    self.output_handler.warning(f"Failed to remove vhost config for {certificate.domain}: {e}")

                # Remove actual certificate files and acme.sh configuration
                try:
                    service.remove_certificate(certificate)
                    removed_count += 1
                except Exception as e:
                    self.output_handler.warning(f"Failed to remove certificate files for {certificate.domain}: {e}")
                    failed_domains.append(certificate.domain)

            except Exception as e:
                self.output_handler.warning(f"Error removing certificate for {certificate.domain}: {e}")
                failed_domains.append(certificate.domain)

        # Restart nginx once after all removals
        try:
            self.nginx_controller.restart()
        except Exception as e:
            self.output_handler.warning(f"Failed to restart nginx: {e}")

        # Report results
        if removed_count > 0:
            self.output_handler.print(f"Successfully removed {removed_count} certificate(s)")
        if failed_domains:
            self.output_handler.warning(f"Failed to remove certificates for: {', '.join(failed_domains)}")
