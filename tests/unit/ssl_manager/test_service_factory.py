"""
Tests for frappe_manager.ssl_manager.service_factory module.

This module tests the create_certificate_service factory function which
creates appropriate SSL certificate services based on certificate configuration.
"""

from unittest.mock import Mock

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.acmesh_certificate_service import AcmeShCertificateService
from frappe_manager.ssl_manager.certificate import CustomCertificate, SSLCertificate
from frappe_manager.ssl_manager.custom_certificate_service import CustomCertificateService
from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.ssl_manager.service_factory import create_certificate_service


class TestCreateCertificateService:
    """Tests for create_certificate_service factory function."""

    def test_returns_noop_service_for_ssl_type_none(self, tmp_path, mock_output_handler, mocker):
        """Test that NoOpCertificateService is returned when ssl_type is 'none'."""
        certificate = SSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.none,
        )

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, NoOpCertificateService)

    def test_returns_custom_service_for_ssl_type_custom(self, tmp_path, mock_output_handler):
        """A custom certificate must go to CustomCertificateService, never to AcmeSh: the factory's
        catch-all fallthrough assumes 'not dev, not none' means Let's Encrypt, and a missing custom
        branch would hand this certificate to acme.sh instead."""
        certificate = CustomCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.custom,
            cert_source=tmp_path / "a.crt",
            key_source=tmp_path / "a.key",
        )

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, CustomCertificateService)
        assert not isinstance(service, AcmeShCertificateService)
        assert service.root_dir == storage_config.ssl_dir / "custom"

    def test_returns_noop_service_when_enabled_false(self, tmp_path, mock_output_handler, mock_letsencrypt_certificate_http01, mocker):
        """Test that NoOpCertificateService is returned when enabled=False."""
        certificate = mock_letsencrypt_certificate_http01
        certificate.enabled = False

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, NoOpCertificateService)

    def test_returns_acmesh_service_for_letsencrypt_http01(self, tmp_path, mock_output_handler, mock_letsencrypt_certificate_http01, mocker):
        """Test that AcmeShCertificateService is returned for Let's Encrypt HTTP-01."""
        certificate = mock_letsencrypt_certificate_http01

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        mocker.patch.object(AcmeShCertificateService, "_ensure_acmesh_installed")

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, AcmeShCertificateService)

    def test_returns_acmesh_service_for_letsencrypt_dns01(self, tmp_path, mock_output_handler, mock_letsencrypt_certificate_dns01, mocker):
        """Test that AcmeShCertificateService is returned for Let's Encrypt DNS-01."""
        certificate = mock_letsencrypt_certificate_dns01

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        mocker.patch.object(AcmeShCertificateService, "_ensure_acmesh_installed")

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, AcmeShCertificateService)

    def test_acmesh_service_receives_correct_paths(self, tmp_path, mock_output_handler, mock_letsencrypt_certificate_http01, mocker):
        """Test that AcmeShCertificateService receives correct directory paths."""
        certificate = mock_letsencrypt_certificate_http01

        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"

        storage_config = Mock()
        storage_config.ssl_dir = ssl_dir
        storage_config.webroot_dir = webroot_dir

        mocker.patch.object(AcmeShCertificateService, "_ensure_acmesh_installed")

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert service.root_dir == ssl_dir / "acmesh"
        assert service.webroot_dir == webroot_dir

    def test_enabled_field_takes_precedence_over_ssl_type(self, tmp_path, mock_output_handler, mock_letsencrypt_certificate_http01, mocker):
        """Test that enabled=False takes precedence even if ssl_type is 'letsencrypt'."""
        certificate = mock_letsencrypt_certificate_http01
        certificate.enabled = False

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, NoOpCertificateService)


class TestServiceFactoryIntegration:
    """Integration tests for service factory with different certificate types."""

    def test_factory_handles_certificate_without_enabled_field(self, tmp_path, mock_output_handler, mocker):
        """Test that factory handles certificates without enabled field."""
        certificate = SSLCertificate(
            domain="example.com",
            ssl_type=SUPPORTED_SSL_TYPES.le,
            challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
        )

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        mocker.patch.object(AcmeShCertificateService, "_ensure_acmesh_installed")

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, AcmeShCertificateService)

    def test_factory_with_custom_acme_client_field(self, tmp_path, mock_output_handler, mock_letsencrypt_certificate_http01, mocker):
        """Test that factory works with certificates that have acme_client field."""
        certificate = mock_letsencrypt_certificate_http01
        certificate.acme_client = "acme.sh"

        storage_config = Mock()
        storage_config.ssl_dir = tmp_path / "ssl"
        storage_config.webroot_dir = tmp_path / "webroot"

        mocker.patch.object(AcmeShCertificateService, "_ensure_acmesh_installed")

        service = create_certificate_service(certificate, storage_config, mock_output_handler)

        assert isinstance(service, AcmeShCertificateService)
