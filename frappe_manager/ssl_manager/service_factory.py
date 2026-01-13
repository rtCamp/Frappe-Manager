"""
Factory for creating SSL certificate service instances.

This module provides a factory function that creates the appropriate SSL certificate
service based on certificate configuration, following the dependency injection pattern.
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

    This factory function encapsulates the logic for choosing the right service
    implementation based on the certificate type and configuration.

    Args:
        certificate: The SSL certificate to create a service for
        storage_config: Storage configuration with paths for SSL operations
        output_handler: Output handler for user-facing messages

    Returns:
        An SSL certificate service instance appropriate for the certificate

    Raises:
        ValueError: If certificate type is unsupported
    """
    # Disabled/no-op certificates
    if certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
        return NoOpCertificateService(
            root_dir=storage_config.ssl_dir,
            output_handler=output_handler,
        )

    # Let's Encrypt certificates
    if certificate.ssl_type == SUPPORTED_SSL_TYPES.le:
        # Check if it's a custom domain with CNAME delegation or if acme.sh is explicitly requested
        if (isinstance(certificate, CustomDomainCertificate) and certificate.delegation_cname) or (
            hasattr(certificate, 'acme_client') and certificate.acme_client == "acme.sh"
        ):
            # Use acme.sh for CNAME delegation support or when explicitly requested
            return AcmeShCertificateService(
                ssl_service_dir=storage_config.ssl_dir,
                webroot_dir=storage_config.webroot_dir,
                output_handler=output_handler,
            )

        # Use certbot for standard Let's Encrypt
        return LetsEncryptCertificateService(
            ssl_service_dir=storage_config.ssl_dir,
            webroot_dir=storage_config.webroot_dir,
            output_handler=output_handler,
        )

    raise ValueError(f"Unsupported SSL certificate type: {certificate.ssl_type}")
