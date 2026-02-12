"""
Tests for frappe_manager.ssl_manager.ssl_certificate_manager module.

This module tests the SSLCertificateManager class which orchestrates
SSL certificate operations by coordinating between multiple services.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotDueForRenewalError,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager


class TestSSLCertificateManagerInitialization:
    """Tests for SSLCertificateManager initialization."""

    def test_init_stores_all_dependencies(
        self,
        mocker,
        mock_certificate,
        mock_ssl_service,
        mock_storage_config,
        mock_link_manager,
        mock_nginx_controller,
        mock_output_handler,
    ):
        """Test that initialization stores all dependencies."""
        mock_logger = mocker.MagicMock()
        mock_logger.child.return_value = mock_logger

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        manager = SSLCertificateManager(
            logger=mock_logger,
            certificates=[mock_certificate],
            service_factory=service_factory,
            link_manager=mock_link_manager,
            nginx_controller=mock_nginx_controller,
            storage_config=mock_storage_config,
            output_handler=mock_output_handler,
        )

        assert mock_certificate in manager.certificates
        assert manager.link_manager == mock_link_manager
        assert manager.nginx_controller == mock_nginx_controller
        assert manager.storage_config == mock_storage_config
        assert manager.output_handler == mock_output_handler

    def test_init_with_empty_certificate_list(
        self, mocker, mock_ssl_service, mock_storage_config, mock_link_manager, mock_nginx_controller, mock_output_handler,
    ):
        """Test that initialization works with empty certificate list."""
        mock_logger = mocker.MagicMock()
        mock_logger.child.return_value = mock_logger

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        manager = SSLCertificateManager(
            logger=mock_logger,
            certificates=[],
            service_factory=service_factory,
            link_manager=mock_link_manager,
            nginx_controller=mock_nginx_controller,
            storage_config=mock_storage_config,
            output_handler=mock_output_handler,
        )

        assert manager.certificates == []
        assert manager.get_primary_certificate() is None

    def test_init_raises_if_link_manager_is_none(
        self, mocker, mock_certificate, mock_ssl_service, mock_storage_config, mock_nginx_controller, mock_output_handler,
    ):
        """Test that initialization raises ValueError if link_manager is None."""
        mock_logger = mocker.MagicMock()
        mock_logger.child.return_value = mock_logger

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        with pytest.raises(ValueError, match="Certificate link manager is required"):
            SSLCertificateManager(
                logger=mock_logger,
                certificates=[mock_certificate],
                service_factory=service_factory,
                link_manager=None,
                nginx_controller=mock_nginx_controller,
                storage_config=mock_storage_config,
                output_handler=mock_output_handler,
            )

    def test_init_raises_if_nginx_controller_is_none(
        self, mocker, mock_certificate, mock_ssl_service, mock_storage_config, mock_link_manager, mock_output_handler,
    ):
        """Test that initialization raises ValueError if nginx_controller is None."""
        mock_logger = mocker.MagicMock()
        mock_logger.child.return_value = mock_logger

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        with pytest.raises(ValueError, match="Nginx controller is required"):
            SSLCertificateManager(
                logger=mock_logger,
                certificates=[mock_certificate],
                service_factory=service_factory,
                link_manager=mock_link_manager,
                nginx_controller=None,
                storage_config=mock_storage_config,
                output_handler=mock_output_handler,
            )


class TestSSLCertificateManagerHasCertificate:
    """Tests for SSLCertificateManager.has_certificate method."""

    def test_has_certificate_returns_true_if_certificate_exists(self, ssl_certificate_manager):
        """Test that has_certificate returns True when certificate exists."""
        # Mock get_certificate_paths to return valid paths
        ssl_certificate_manager.link_manager.get_certificate_paths.return_value = (
            Path("/privkey.pem"),
            Path("/fullchain.pem"),
        )

        result = ssl_certificate_manager.has_certificate()

        assert result is True

    def test_has_certificate_returns_false_if_certificate_not_found(self, ssl_certificate_manager):
        """Test that has_certificate returns False when certificate doesn't exist."""
        # Mock get_certificate_paths to raise FileNotFoundError
        ssl_certificate_manager.link_manager.get_certificate_paths.side_effect = FileNotFoundError()

        result = ssl_certificate_manager.has_certificate()

        assert result is False

    def test_has_certificate_with_specific_domain(self, ssl_certificate_manager):
        """Test that has_certificate works with specific domain parameter."""
        ssl_certificate_manager.link_manager.get_certificate_paths.return_value = (
            Path("/privkey.pem"),
            Path("/fullchain.pem"),
        )

        result = ssl_certificate_manager.has_certificate(domain="example.com")

        assert result is True
        ssl_certificate_manager.link_manager.get_certificate_paths.assert_called_with("example.com")


class TestSSLCertificateManagerGetCertificatePaths:
    """Tests for SSLCertificateManager.get_certificate_paths method."""

    def test_get_certificate_paths_returns_paths_from_link_manager(self, ssl_certificate_manager):
        """Test that get_certificate_paths returns paths from link manager."""
        expected_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        ssl_certificate_manager.link_manager.get_certificate_paths.return_value = expected_paths

        result = ssl_certificate_manager.get_certificate_paths()

        assert result == expected_paths
        primary_cert = ssl_certificate_manager.get_primary_certificate()
        ssl_certificate_manager.link_manager.get_certificate_paths.assert_called_once_with(primary_cert.domain)

    def test_get_certificate_paths_raises_not_found_error(self, ssl_certificate_manager):
        """Test that get_certificate_paths raises SSLCertificateNotFoundError."""
        ssl_certificate_manager.link_manager.get_certificate_paths.side_effect = FileNotFoundError()

        with pytest.raises(SSLCertificateNotFoundError) as exc_info:
            ssl_certificate_manager.get_certificate_paths()

        primary_cert = ssl_certificate_manager.get_primary_certificate()
        assert primary_cert.domain in str(exc_info.value)

    def test_get_certificate_paths_with_specific_domain(self, ssl_certificate_manager):
        """Test that get_certificate_paths works with specific domain parameter."""
        expected_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        ssl_certificate_manager.link_manager.get_certificate_paths.return_value = expected_paths

        result = ssl_certificate_manager.get_certificate_paths(domain="custom.com")

        assert result == expected_paths
        ssl_certificate_manager.link_manager.get_certificate_paths.assert_called_once_with("custom.com")


class TestSSLCertificateManagerGetCertificateExpiry:
    """Tests for SSLCertificateManager.get_certificate_expiry method."""

    def test_get_certificate_expiry_returns_expiry_date(self, mocker, ssl_certificate_manager):
        """Test that get_certificate_expiry returns the certificate expiry date."""
        # Mock get_certificate_paths
        ssl_certificate_manager.link_manager.get_certificate_paths.return_value = (
            Path("/privkey.pem"),
            Path("/fullchain.pem"),
        )

        # Mock get_certificate_expiry_date
        expected_expiry = datetime(2025, 12, 31, 23, 59, 59)
        mock_get_expiry = mocker.patch(
            "frappe_manager.ssl_manager.ssl_certificate_manager.get_certificate_expiry_date",
            return_value=expected_expiry,
        )

        result = ssl_certificate_manager.get_certificate_expiry()

        assert result == expected_expiry
        mock_get_expiry.assert_called_once_with(Path("/fullchain.pem"))


class TestSSLCertificateManagerNeedsRenewal:
    """Tests for SSLCertificateManager.needs_renewal method."""

    def test_needs_renewal_returns_true_if_expiring_soon(self, mocker, ssl_certificate_manager):
        """Test that needs_renewal returns True if certificate expires soon."""
        # Mock expiry date to be 10 days from now (less than SSL_RENEW_BEFORE_DAYS)
        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        # Mock SSL_RENEW_BEFORE_DAYS to be 30 days
        mocker.patch("frappe_manager.ssl_manager.ssl_certificate_manager.SSL_RENEW_BEFORE_DAYS", 30)

        result = ssl_certificate_manager.needs_renewal()

        assert result is True

    def test_needs_renewal_returns_false_if_not_expiring_soon(self, mocker, ssl_certificate_manager):
        """Test that needs_renewal returns False if certificate has plenty of time."""
        # Mock expiry date to be 60 days from now (more than SSL_RENEW_BEFORE_DAYS)
        expiry_date = datetime.now() + timedelta(days=60)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        # Mock SSL_RENEW_BEFORE_DAYS to be 30 days
        mocker.patch("frappe_manager.ssl_manager.ssl_certificate_manager.SSL_RENEW_BEFORE_DAYS", 30)

        result = ssl_certificate_manager.needs_renewal()

        assert result is False

    def test_needs_renewal_handles_timezone_aware_expiry(self, mocker, ssl_certificate_manager):
        """Test that needs_renewal correctly handles timezone-aware expiry dates."""
        from datetime import timezone

        # Mock expiry date with timezone
        expiry_date = datetime.now(tz=timezone.utc) + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        # Mock SSL_RENEW_BEFORE_DAYS to be 30 days
        mocker.patch("frappe_manager.ssl_manager.ssl_certificate_manager.SSL_RENEW_BEFORE_DAYS", 30)

        result = ssl_certificate_manager.needs_renewal()

        assert result is True


class TestSSLCertificateManagerAddCertificate:
    """Tests for SSLCertificateManager.add_certificate method."""

    def test_add_certificate_success(self, mocker, ssl_certificate_manager, mock_letsencrypt_certificate_http01):
        """Test successful certificate addition."""
        # Mock the service
        mock_service = MagicMock()
        mock_service.generate_certificate.return_value = (Path("/key.pem"), Path("/fullchain.pem"))

        # Mock service factory
        ssl_certificate_manager.service_factory = MagicMock(return_value=mock_service)

        # Mock vhost manager
        ssl_certificate_manager.vhost_manager = MagicMock()

        # Add new certificate
        new_cert = mock_letsencrypt_certificate_http01
        new_cert.domain = "new-domain.com"

        ssl_certificate_manager.add_certificate(new_cert)

        assert new_cert in ssl_certificate_manager.certificates
        assert "new-domain.com" in ssl_certificate_manager.services

        mock_service.generate_certificate.assert_called_once_with(new_cert, dry_run=False)

        # Verify symlinks were created
        ssl_certificate_manager.link_manager.link_certificate.assert_called_once()

        # Verify vhost redirect was enabled
        ssl_certificate_manager.vhost_manager.enable_https_redirect.assert_called_once_with("new-domain.com")

        # Verify nginx was restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_add_certificate_raises_if_domain_exists(
        self, ssl_certificate_manager, mock_letsencrypt_certificate_http01,
    ):
        """Test that adding duplicate domain raises ValueError."""
        # Try to add certificate for existing domain
        duplicate_cert = mock_letsencrypt_certificate_http01
        duplicate_cert.domain = ssl_certificate_manager.certificates[0].domain

        with pytest.raises(ValueError, match="already exists"):
            ssl_certificate_manager.add_certificate(duplicate_cert)

    def test_add_certificate_dry_run_uses_staging(
        self, mocker, tmp_path, ssl_certificate_manager, mock_letsencrypt_certificate_http01, monkeypatch,
    ):
        """Test that dry run mode uses staging server."""
        cert_dir = tmp_path / "ssl" / "acmesh" / "test.com"
        cert_dir.mkdir(parents=True)
        key_path = cert_dir / "key.pem"
        fullchain_path = cert_dir / "fullchain.pem"
        key_path.touch()
        fullchain_path.touch()

        mock_service = MagicMock()
        mock_service.generate_certificate.return_value = (key_path, fullchain_path)

        ssl_certificate_manager.service_factory = MagicMock(return_value=mock_service)
        ssl_certificate_manager.vhost_manager = MagicMock()
        ssl_certificate_manager.storage_config.ssl_dir = tmp_path / "ssl"

        new_cert = mock_letsencrypt_certificate_http01
        new_cert.domain = "test.com"

        monkeypatch.delenv("FM_LETSENCRYPT_STAGING", raising=False)

        ssl_certificate_manager.add_certificate(new_cert, dry_run=True)

        assert new_cert not in ssl_certificate_manager.certificates
        ssl_certificate_manager.link_manager.link_certificate.assert_not_called()
        ssl_certificate_manager.nginx_controller.restart.assert_not_called()

    def test_add_certificate_calls_config_save_callback(
        self, ssl_certificate_manager, mock_letsencrypt_certificate_http01,
    ):
        """Test that config save callback is called after adding certificate."""
        mock_service = MagicMock()
        mock_service.generate_certificate.return_value = (Path("/key.pem"), Path("/fullchain.pem"))

        ssl_certificate_manager.service_factory = MagicMock(return_value=mock_service)
        ssl_certificate_manager.vhost_manager = MagicMock()

        # Set up callback
        mock_callback = MagicMock()
        ssl_certificate_manager.config_save_callback = mock_callback

        new_cert = mock_letsencrypt_certificate_http01
        new_cert.domain = "callback-test.com"

        ssl_certificate_manager.add_certificate(new_cert)

        # Verify callback was called
        mock_callback.assert_called_once()


class TestSSLCertificateManagerRemoveCertificate:
    """Tests for SSLCertificateManager.remove_certificate_by_domain method."""

    def test_remove_certificate_success(self, ssl_certificate_manager):
        """Test successful certificate removal."""
        domain = ssl_certificate_manager.certificates[0].domain

        # Mock the service
        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service

        # Mock vhost manager
        ssl_certificate_manager.vhost_manager = MagicMock()

        ssl_certificate_manager.remove_certificate_by_domain(domain)

        # Verify certificate was removed from list
        assert len(ssl_certificate_manager.certificates) == 0

        # Verify service was removed
        assert domain not in ssl_certificate_manager.services

        # Verify symlinks were removed
        ssl_certificate_manager.link_manager.unlink_certificate.assert_called_once_with(domain, alias_domains=None)

        # Verify vhost redirect was disabled
        ssl_certificate_manager.vhost_manager.disable_https_redirect.assert_called_once_with(domain)

        # Verify service remove was called
        mock_service.remove_certificate.assert_called_once()

        # Verify nginx was restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_remove_certificate_raises_if_not_found(self, ssl_certificate_manager):
        """Test that removing non-existent domain raises error."""
        with pytest.raises(SSLCertificateNotFoundError):
            ssl_certificate_manager.remove_certificate_by_domain("nonexistent.com")

    def test_remove_certificate_calls_config_save_callback(self, ssl_certificate_manager):
        """Test that config save callback is called after removal."""
        domain = ssl_certificate_manager.certificates[0].domain

        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service
        ssl_certificate_manager.vhost_manager = MagicMock()

        # Set up callback
        mock_callback = MagicMock()
        ssl_certificate_manager.config_save_callback = mock_callback

        ssl_certificate_manager.remove_certificate_by_domain(domain)

        # Verify callback was called
        mock_callback.assert_called_once()


class TestSSLCertificateManagerGenerateAllCertificates:
    """Tests for SSLCertificateManager.generate_all_certificates method."""

    def test_generate_all_certificates_success(self, ssl_certificate_manager, mock_letsencrypt_certificate_http01):
        """Test generating certificates for all domains."""
        # Add multiple certificates
        cert2 = mock_letsencrypt_certificate_http01
        cert2.domain = "second.com"
        ssl_certificate_manager.certificates.append(cert2)

        # Mock services for both domains
        mock_service1 = MagicMock()
        mock_service1.generate_certificate.return_value = (Path("/key1.pem"), Path("/fullchain1.pem"))

        mock_service2 = MagicMock()
        mock_service2.generate_certificate.return_value = (Path("/key2.pem"), Path("/fullchain2.pem"))

        ssl_certificate_manager.services[ssl_certificate_manager.certificates[0].domain] = mock_service1
        ssl_certificate_manager.services["second.com"] = mock_service2

        ssl_certificate_manager.generate_all_certificates()

        # Verify both services were called
        mock_service1.generate_certificate.assert_called_once()
        mock_service2.generate_certificate.assert_called_once()

        # Verify symlinks were created for both
        assert ssl_certificate_manager.link_manager.link_certificate.call_count == 2

        # Verify nginx was restarted once
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_generate_all_certificates_raises_if_no_certs(self, ssl_certificate_manager):
        """Test that generating with no certificates raises ValueError."""
        ssl_certificate_manager.certificates = []

        with pytest.raises(ValueError, match="No certificates configured"):
            ssl_certificate_manager.generate_all_certificates()

    def test_generate_all_certificates_raises_if_service_not_found(self, ssl_certificate_manager):
        """Test that missing service raises RuntimeError."""
        # Remove service for domain
        domain = ssl_certificate_manager.certificates[0].domain
        del ssl_certificate_manager.services[domain]

        with pytest.raises(RuntimeError, match="No service found"):
            ssl_certificate_manager.generate_all_certificates()


class TestSSLCertificateManagerListCertificates:
    """Tests for SSLCertificateManager.list_certificates method."""

    def test_list_certificates_with_existing_cert(self, mocker, ssl_certificate_manager):
        """Test listing certificates when certificate exists."""
        # Mock get_certificate_paths to return valid paths
        ssl_certificate_manager.link_manager.get_certificate_paths.return_value = (
            Path("/privkey.pem"),
            Path("/fullchain.pem"),
        )

        # Mock expiry date
        expiry_date = datetime.now() + timedelta(days=60)
        mocker.patch(
            "frappe_manager.ssl_manager.ssl_certificate_manager.get_certificate_expiry_date", return_value=expiry_date,
        )

        # Mock SSL_RENEW_BEFORE_DAYS
        mocker.patch("frappe_manager.ssl_manager.ssl_certificate_manager.SSL_RENEW_BEFORE_DAYS", 30)

        result = ssl_certificate_manager.list_certificates()

        assert len(result) == 1
        assert result[0]["domain"] == ssl_certificate_manager.certificates[0].domain
        assert result[0]["exists"] is True
        assert result[0]["expiry_date"] == expiry_date
        assert result[0]["needs_renewal"] is False
        assert result[0]["days_until_expiry"] > 0

    def test_list_certificates_with_missing_cert(self, ssl_certificate_manager):
        """Test listing certificates when certificate doesn't exist."""
        # Mock get_certificate_paths to raise FileNotFoundError
        ssl_certificate_manager.link_manager.get_certificate_paths.side_effect = FileNotFoundError()

        result = ssl_certificate_manager.list_certificates()

        assert len(result) == 1
        assert result[0]["domain"] == ssl_certificate_manager.certificates[0].domain
        assert result[0]["exists"] is False
        assert result[0]["expiry_date"] is None
        assert result[0]["needs_renewal"] is False


class TestSSLCertificateManagerRenewCertificate:
    """Tests for SSLCertificateManager.renew_certificate method."""

    def test_renew_certificate_success(self, mocker, ssl_certificate_manager):
        """Test successful certificate renewal for primary certificate."""
        # Mock needs_renewal to return True
        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=True)

        # Mock get_certificate_expiry for SSLCertificateNotDueForRenewalError check
        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        # Mock get_certificate_paths
        mocker.patch.object(
            ssl_certificate_manager, "get_certificate_paths", return_value=(Path("/key.pem"), Path("/fullchain.pem")),
        )

        # Mock the service
        domain = ssl_certificate_manager.certificates[0].domain
        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service

        ssl_certificate_manager.renew_certificate()

        # Verify service renew was called
        mock_service.renew_certificate.assert_called_once()

        # Verify symlinks were updated
        ssl_certificate_manager.link_manager.link_certificate.assert_called_once()

        # Verify nginx was restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_renew_certificate_with_specific_domain(self, mocker, ssl_certificate_manager):
        """Test certificate renewal for specific domain."""
        domain = ssl_certificate_manager.certificates[0].domain

        # Mock needs_renewal to return True
        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=True)

        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        mocker.patch.object(
            ssl_certificate_manager, "get_certificate_paths", return_value=(Path("/key.pem"), Path("/fullchain.pem")),
        )

        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service

        ssl_certificate_manager.renew_certificate(domain=domain)

        mock_service.renew_certificate.assert_called_once_with(ssl_certificate_manager.certificates[0], dry_run=False)

    def test_renew_certificate_raises_if_not_due(self, mocker, ssl_certificate_manager):
        """Test that renewal raises error if certificate not due for renewal."""
        # Mock needs_renewal to return False
        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=False)

        expiry_date = datetime.now() + timedelta(days=60)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        with pytest.raises(SSLCertificateNotDueForRenewalError):
            ssl_certificate_manager.renew_certificate()

    def test_renew_certificate_raises_if_domain_not_found(self, ssl_certificate_manager):
        """Test that renewal raises error if domain doesn't exist."""
        with pytest.raises(SSLCertificateNotFoundError):
            ssl_certificate_manager.renew_certificate(domain="nonexistent.com")

    def test_renew_certificate_dry_run_uses_staging(self, mocker, ssl_certificate_manager, monkeypatch):
        """Test that dry run mode uses staging server and skips system modifications."""
        # Mock needs_renewal to return True
        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=True)

        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        domain = ssl_certificate_manager.certificates[0].domain
        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service

        # Clear any existing FM_LETSENCRYPT_STAGING
        monkeypatch.delenv("FM_LETSENCRYPT_STAGING", raising=False)

        ssl_certificate_manager.renew_certificate(dry_run=True)

        # Verify service renew was called
        mock_service.renew_certificate.assert_called_once()

        # Verify symlinks were NOT updated (dry run)
        ssl_certificate_manager.link_manager.link_certificate.assert_not_called()

        # Verify nginx was NOT restarted (dry run)
        ssl_certificate_manager.nginx_controller.restart.assert_not_called()

    def test_renew_certificate_handles_symlink_recreation_failure(self, mocker, ssl_certificate_manager):
        """Test that renewal succeeds even if symlink recreation fails."""
        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=True)

        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        # Mock get_certificate_paths to raise error (simulating symlink failure)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_paths", side_effect=Exception("Symlink error"))

        domain = ssl_certificate_manager.certificates[0].domain
        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service

        # Should not raise - renewal still succeeds even if symlink recreation fails
        ssl_certificate_manager.renew_certificate()

        # Verify service renew was called
        mock_service.renew_certificate.assert_called_once()

        # Verify nginx was still restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()


class TestSSLCertificateManagerRenewAllCertificates:
    """Tests for SSLCertificateManager.renew_all_certificates method."""

    def test_renew_all_certificates_success(self, mocker, ssl_certificate_manager, mock_letsencrypt_certificate_http01):
        """Test renewing all certificates that are due for renewal."""
        # Add second certificate
        cert2 = mock_letsencrypt_certificate_http01
        cert2.domain = "second.com"
        ssl_certificate_manager.certificates.append(cert2)

        # Mock needs_renewal to return True for both
        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=True)

        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        mocker.patch.object(
            ssl_certificate_manager, "get_certificate_paths", return_value=(Path("/key.pem"), Path("/fullchain.pem")),
        )

        # Mock services for both domains
        mock_service1 = MagicMock()
        mock_service2 = MagicMock()
        ssl_certificate_manager.services[ssl_certificate_manager.certificates[0].domain] = mock_service1
        ssl_certificate_manager.services["second.com"] = mock_service2

        ssl_certificate_manager.renew_all_certificates()

        # Verify both services were called
        mock_service1.renew_certificate.assert_called_once()
        mock_service2.renew_certificate.assert_called_once()

        # Verify symlinks were updated for both
        assert ssl_certificate_manager.link_manager.link_certificate.call_count == 2

        # Verify nginx was restarted once
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_renew_all_certificates_skips_not_due(
        self, mocker, ssl_certificate_manager, mock_letsencrypt_certificate_http01,
    ):
        """Test that certificates not due for renewal are skipped."""
        # Add second certificate
        cert2 = mock_letsencrypt_certificate_http01
        cert2.domain = "second.com"
        ssl_certificate_manager.certificates.append(cert2)

        # Mock first cert needs renewal, second doesn't
        def needs_renewal_side_effect(domain):
            return domain == ssl_certificate_manager.certificates[0].domain

        mocker.patch.object(ssl_certificate_manager, "needs_renewal", side_effect=needs_renewal_side_effect)

        expiry_date_soon = datetime.now() + timedelta(days=10)
        expiry_date_later = datetime.now() + timedelta(days=60)

        def get_expiry_side_effect(domain):
            if domain == ssl_certificate_manager.certificates[0].domain:
                return expiry_date_soon
            return expiry_date_later

        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", side_effect=get_expiry_side_effect)

        mocker.patch.object(
            ssl_certificate_manager, "get_certificate_paths", return_value=(Path("/key.pem"), Path("/fullchain.pem")),
        )

        # Mock services
        mock_service1 = MagicMock()
        mock_service2 = MagicMock()
        ssl_certificate_manager.services[ssl_certificate_manager.certificates[0].domain] = mock_service1
        ssl_certificate_manager.services["second.com"] = mock_service2

        ssl_certificate_manager.renew_all_certificates()

        # Verify only first service was called
        mock_service1.renew_certificate.assert_called_once()
        mock_service2.renew_certificate.assert_not_called()

    def test_renew_all_certificates_raises_if_no_certs(self, ssl_certificate_manager):
        """Test that renewing with no certificates raises ValueError."""
        ssl_certificate_manager.certificates = []

        with pytest.raises(ValueError, match="No certificates configured"):
            ssl_certificate_manager.renew_all_certificates()

    def test_renew_all_certificates_continues_on_error(
        self, mocker, ssl_certificate_manager, mock_letsencrypt_certificate_http01,
    ):
        """Test that renewal continues even if one certificate fails."""
        # Add second certificate
        cert2 = mock_letsencrypt_certificate_http01
        cert2.domain = "second.com"
        ssl_certificate_manager.certificates.append(cert2)

        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=True)

        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        mocker.patch.object(
            ssl_certificate_manager, "get_certificate_paths", return_value=(Path("/key.pem"), Path("/fullchain.pem")),
        )

        # Mock first service to raise error, second to succeed
        mock_service1 = MagicMock()
        mock_service1.renew_certificate.side_effect = Exception("Renewal failed")
        mock_service2 = MagicMock()

        ssl_certificate_manager.services[ssl_certificate_manager.certificates[0].domain] = mock_service1
        ssl_certificate_manager.services["second.com"] = mock_service2

        # Should not raise - continues processing
        ssl_certificate_manager.renew_all_certificates()

        # Verify both services were attempted
        mock_service1.renew_certificate.assert_called_once()
        mock_service2.renew_certificate.assert_called_once()

    def test_renew_all_certificates_dry_run_mode(self, mocker, ssl_certificate_manager, monkeypatch):
        """Test that dry run mode uses staging server."""
        mocker.patch.object(ssl_certificate_manager, "needs_renewal", return_value=True)

        expiry_date = datetime.now() + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, "get_certificate_expiry", return_value=expiry_date)

        domain = ssl_certificate_manager.certificates[0].domain
        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service

        monkeypatch.delenv("FM_LETSENCRYPT_STAGING", raising=False)

        ssl_certificate_manager.renew_all_certificates(dry_run=True)

        # Verify service renew was called
        mock_service.renew_certificate.assert_called_once()

        # Verify symlinks were NOT updated (dry run)
        ssl_certificate_manager.link_manager.link_certificate.assert_not_called()

        # Verify nginx was NOT restarted (dry run)
        ssl_certificate_manager.nginx_controller.restart.assert_not_called()


class TestSSLCertificateManagerRemoveCertificateMethod:
    """Tests for SSLCertificateManager.remove_certificate method (file removal only)."""

    def test_remove_certificate_primary_domain(self, ssl_certificate_manager):
        """Test removing primary certificate files using remove_certificate method."""
        domain = ssl_certificate_manager.certificates[0].domain

        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service
        ssl_certificate_manager.vhost_manager = MagicMock()

        # Call without domain parameter (should use primary)
        ssl_certificate_manager.remove_certificate()

        # Verify certificate is still in list (remove_certificate only removes files, not from list)
        assert len(ssl_certificate_manager.certificates) == 1

        # Verify symlinks were removed
        ssl_certificate_manager.link_manager.unlink_certificate.assert_called_once()

        # Verify vhost redirect was disabled
        ssl_certificate_manager.vhost_manager.disable_https_redirect.assert_called_once_with(domain)

        # Verify service remove was called
        mock_service.remove_certificate.assert_called_once()

        # Verify nginx was restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_remove_certificate_specific_domain(self, ssl_certificate_manager, mock_letsencrypt_certificate_http01):
        """Test removing specific domain files using remove_certificate method."""
        # Add second certificate
        cert2 = mock_letsencrypt_certificate_http01
        cert2.domain = "second.com"
        ssl_certificate_manager.certificates.append(cert2)

        # Setup services for both
        domain1 = ssl_certificate_manager.certificates[0].domain
        mock_service1 = MagicMock()
        mock_service2 = MagicMock()
        ssl_certificate_manager.services[domain1] = mock_service1
        ssl_certificate_manager.services["second.com"] = mock_service2
        ssl_certificate_manager.vhost_manager = MagicMock()

        # Remove second domain
        ssl_certificate_manager.remove_certificate(domain="second.com")

        # Verify both certs still in list (remove_certificate only removes files)
        assert len(ssl_certificate_manager.certificates) == 2

        # Verify service remove was called on correct service
        mock_service2.remove_certificate.assert_called_once()
        mock_service1.remove_certificate.assert_not_called()

    def test_remove_certificate_raises_if_no_primary(self, ssl_certificate_manager):
        """Test that remove_certificate raises error if no primary certificate."""
        ssl_certificate_manager.certificates = []

        with pytest.raises(ValueError, match="No primary certificate configured"):
            ssl_certificate_manager.remove_certificate()

    def test_remove_certificate_raises_if_domain_not_found(self, ssl_certificate_manager):
        """Test that remove_certificate raises error if domain doesn't exist."""
        with pytest.raises(SSLCertificateNotFoundError):
            ssl_certificate_manager.remove_certificate(domain="nonexistent.com")

    def test_remove_certificate_disables_vhost_redirect(self, ssl_certificate_manager):
        """Test that remove_certificate disables HTTPS redirect."""
        domain = ssl_certificate_manager.certificates[0].domain

        mock_service = MagicMock()
        ssl_certificate_manager.services[domain] = mock_service
        ssl_certificate_manager.vhost_manager = MagicMock()

        ssl_certificate_manager.remove_certificate(domain=domain)

        # Verify vhost redirect was disabled
        ssl_certificate_manager.vhost_manager.disable_https_redirect.assert_called_once_with(domain)
