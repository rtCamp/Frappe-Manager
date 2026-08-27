"""
Factory for creating SSL certificate service instances.

This module provides a factory function that creates the appropriate SSL certificate
service based on certificate configuration, following the dependency injection pattern.
"""

from frappe_manager.output_manager import OutputHandler
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.acmesh_certificate_service import AcmeShCertificateService
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig


def create_certificate_service(
    certificate: SSLCertificate,
    storage_config: SSLStorageConfig,
    output_handler: OutputHandler,
    bench_config=None,
) -> SSLCertificateService:
    """
    Create an appropriate SSL certificate service based on certificate configuration.

    This factory function uses acme.sh for all Let's Encrypt certificates.

    Args:
        certificate: The SSL certificate to create a service for
        storage_config: Storage configuration with paths for SSL operations
        output_handler: Output handler for user-facing messages
        bench_config: The owning bench's config, so DNS-01 issuance can read that bench's
            `[ssl.dns_providers]`. None for standalone certificates, which have no bench.

    Returns:
        An SSL certificate service instance appropriate for the certificate

    Raises:
        ValueError: If certificate configuration is invalid
    """
    if hasattr(certificate, "ssl_type") and certificate.ssl_type == SUPPORTED_SSL_TYPES.none:
        return NoOpCertificateService(
            root_dir=storage_config.ssl_dir,
            output_handler=output_handler,
        )

    if hasattr(certificate, "enabled") and not certificate.enabled:
        return NoOpCertificateService(
            root_dir=storage_config.ssl_dir,
            output_handler=output_handler,
        )

    if certificate.ssl_type == SUPPORTED_SSL_TYPES.dev:
        from frappe_manager.ssl_manager.dev_certificate_service import DevCertificateService

        return DevCertificateService(
            ssl_service_dir=storage_config.ssl_dir,
            output_handler=output_handler,
        )

    return AcmeShCertificateService(
        ssl_service_dir=storage_config.ssl_dir,
        webroot_dir=storage_config.webroot_dir,
        output_handler=output_handler,
        bench_config=bench_config,
    )
