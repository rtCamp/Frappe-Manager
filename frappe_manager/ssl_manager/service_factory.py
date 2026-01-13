"""
Factory for creating SSL certificate service instances.

This module provides a factory function that creates the appropriate SSL certificate
service based on certificate configuration (acme_client), following the dependency injection pattern.
"""

from pathlib import Path

from frappe_manager.output_manager import OutputHandler
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.acmesh_certificate_service import AcmeShCertificateService
from frappe_manager.ssl_manager.certificate import CustomDomainCertificate, SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate_service import LetsEncryptCertificateService
from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig


def create_certificate_service(
    certificate: SSLCertificate,
    storage_config: SSLStorageConfig,
    output_handler: OutputHandler,
) -> SSLCertificateService:
    """
    Create an appropriate SSL certificate service based on certificate configuration.

    This factory function chooses the service based on acme_client field:
    - "acme.sh" -> AcmeShCertificateService
    - "certbot" -> LetsEncryptCertificateService
    - None/disabled -> NoOpCertificateService

    Args:
        certificate: The SSL certificate to create a service for
        storage_config: Storage configuration with paths for SSL operations
        output_handler: Output handler for user-facing messages

    Returns:
        An SSL certificate service instance appropriate for the certificate

    Raises:
        ValueError: If certificate configuration is invalid
    """
    # Check if SSL is disabled (for backward compatibility with ssl_type field)
    if hasattr(certificate, 'ssl_type') and certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
        return NoOpCertificateService(
            root_dir=storage_config.ssl_dir,
            output_handler=output_handler,
        )

    # Check if certificate is explicitly disabled via enabled field
    if hasattr(certificate, 'enabled') and not certificate.enabled:
        return NoOpCertificateService(
            root_dir=storage_config.ssl_dir,
            output_handler=output_handler,
        )

    # Determine service based on acme_client field
    acme_client = getattr(certificate, 'acme_client', 'acme.sh')  # Default to acme.sh

    if acme_client == "acme.sh":
        # Use acme.sh service
        return AcmeShCertificateService(
            ssl_service_dir=storage_config.ssl_dir,
            webroot_dir=storage_config.webroot_dir,
            output_handler=output_handler,
        )

    elif acme_client == "certbot":
        # Use certbot service
        return LetsEncryptCertificateService(
            ssl_service_dir=storage_config.ssl_dir,
            webroot_dir=storage_config.webroot_dir,
            output_handler=output_handler,
        )

    else:
        raise ValueError(f"Unsupported ACME client: {acme_client}. Supported: 'certbot', 'acme.sh'")
