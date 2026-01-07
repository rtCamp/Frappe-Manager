"""
Tests for frappe_manager.ssl_manager.ssl_certificate_manager module.

This module tests the SSLCertificateManager class which orchestrates
SSL certificate operations by coordinating between multiple services.
"""

import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock, call
from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateNotFoundError,
    SSLCertificateNotDueForRenewalError,
)


class TestSSLCertificateManagerInitialization:
    """Tests for SSLCertificateManager initialization."""

    def test_init_stores_all_dependencies(
        self, mock_certificate, mock_ssl_service, mock_link_manager, mock_nginx_controller
    ):
        """Test that initialization stores all dependencies."""
        manager = SSLCertificateManager(
            certificate=mock_certificate,
            service=mock_ssl_service,
            link_manager=mock_link_manager,
            nginx_controller=mock_nginx_controller,
        )

        assert manager.certificate == mock_certificate
        assert manager.service == mock_ssl_service
        assert manager.link_manager == mock_link_manager
        assert manager.nginx_controller == mock_nginx_controller

    def test_init_raises_if_certificate_is_none(self, mock_ssl_service, mock_link_manager, mock_nginx_controller):
        """Test that initialization raises ValueError if certificate is None."""
        with pytest.raises(ValueError, match="Certificate configuration is required"):
            SSLCertificateManager(
                certificate=None,
                service=mock_ssl_service,
                link_manager=mock_link_manager,
                nginx_controller=mock_nginx_controller,
            )

    def test_init_raises_if_service_is_none(self, mock_certificate, mock_link_manager, mock_nginx_controller):
        """Test that initialization raises ValueError if service is None."""
        with pytest.raises(ValueError, match="Certificate service is required"):
            SSLCertificateManager(
                certificate=mock_certificate,
                service=None,
                link_manager=mock_link_manager,
                nginx_controller=mock_nginx_controller,
            )

    def test_init_raises_if_link_manager_is_none(self, mock_certificate, mock_ssl_service, mock_nginx_controller):
        """Test that initialization raises ValueError if link_manager is None."""
        with pytest.raises(ValueError, match="Certificate link manager is required"):
            SSLCertificateManager(
                certificate=mock_certificate,
                service=mock_ssl_service,
                link_manager=None,
                nginx_controller=mock_nginx_controller,
            )

    def test_init_raises_if_nginx_controller_is_none(self, mock_certificate, mock_ssl_service, mock_link_manager):
        """Test that initialization raises ValueError if nginx_controller is None."""
        with pytest.raises(ValueError, match="Nginx controller is required"):
            SSLCertificateManager(
                certificate=mock_certificate,
                service=mock_ssl_service,
                link_manager=mock_link_manager,
                nginx_controller=None,
            )


class TestSSLCertificateManagerSetCertificate:
    """Tests for SSLCertificateManager.set_certificate method."""

    def test_set_certificate_updates_certificate(self, ssl_certificate_manager):
        """Test that set_certificate updates the certificate configuration."""
        from frappe_manager.ssl_manager.certificate import SSLCertificate
        from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES

        new_certificate = SSLCertificate(domain="newdomain.com", ssl_type=SUPPORTED_SSL_TYPES.le)

        ssl_certificate_manager.set_certificate(new_certificate)

        assert ssl_certificate_manager.certificate == new_certificate
        assert ssl_certificate_manager.certificate.domain == "newdomain.com"


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


class TestSSLCertificateManagerGetCertificatePaths:
    """Tests for SSLCertificateManager.get_certificate_paths method."""

    def test_get_certificate_paths_returns_paths_from_link_manager(self, ssl_certificate_manager):
        """Test that get_certificate_paths returns paths from link manager."""
        expected_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        ssl_certificate_manager.link_manager.get_certificate_paths.return_value = expected_paths

        result = ssl_certificate_manager.get_certificate_paths()

        assert result == expected_paths
        ssl_certificate_manager.link_manager.get_certificate_paths.assert_called_once_with(
            ssl_certificate_manager.certificate.domain
        )

    def test_get_certificate_paths_raises_not_found_error(self, ssl_certificate_manager):
        """Test that get_certificate_paths raises SSLCertificateNotFoundError."""
        ssl_certificate_manager.link_manager.get_certificate_paths.side_effect = FileNotFoundError()

        with pytest.raises(SSLCertificateNotFoundError) as exc_info:
            ssl_certificate_manager.get_certificate_paths()

        assert ssl_certificate_manager.certificate.domain in str(exc_info.value)


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
            'frappe_manager.ssl_manager.ssl_certificate_manager.get_certificate_expiry_date',
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
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_expiry', return_value=expiry_date)

        # Mock SSL_RENEW_BEFORE_DAYS to be 30 days
        mocker.patch('frappe_manager.ssl_manager.ssl_certificate_manager.SSL_RENEW_BEFORE_DAYS', 30)

        result = ssl_certificate_manager.needs_renewal()

        assert result is True

    def test_needs_renewal_returns_false_if_not_expiring_soon(self, mocker, ssl_certificate_manager):
        """Test that needs_renewal returns False if certificate has plenty of time."""
        # Mock expiry date to be 60 days from now (more than SSL_RENEW_BEFORE_DAYS)
        expiry_date = datetime.now() + timedelta(days=60)
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_expiry', return_value=expiry_date)

        # Mock SSL_RENEW_BEFORE_DAYS to be 30 days
        mocker.patch('frappe_manager.ssl_manager.ssl_certificate_manager.SSL_RENEW_BEFORE_DAYS', 30)

        result = ssl_certificate_manager.needs_renewal()

        assert result is False

    def test_needs_renewal_handles_timezone_aware_expiry(self, mocker, ssl_certificate_manager):
        """Test that needs_renewal correctly handles timezone-aware expiry dates."""
        from datetime import timezone

        # Mock expiry date with timezone
        expiry_date = datetime.now(tz=timezone.utc) + timedelta(days=10)
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_expiry', return_value=expiry_date)

        # Mock SSL_RENEW_BEFORE_DAYS to be 30 days
        mocker.patch('frappe_manager.ssl_manager.ssl_certificate_manager.SSL_RENEW_BEFORE_DAYS', 30)

        result = ssl_certificate_manager.needs_renewal()

        assert result is True


class TestSSLCertificateManagerGenerateCertificate:
    """Tests for SSLCertificateManager.generate_certificate method."""

    def test_generate_certificate_calls_service_and_creates_symlinks(self, ssl_certificate_manager):
        """Test that generate_certificate orchestrates all required operations."""
        # Mock service.generate_certificate to return paths
        expected_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        ssl_certificate_manager.service.generate_certificate.return_value = expected_paths

        ssl_certificate_manager.generate_certificate()

        # Verify service was called
        ssl_certificate_manager.service.generate_certificate.assert_called_once_with(
            ssl_certificate_manager.certificate, None
        )

        # Verify link_manager was called
        ssl_certificate_manager.link_manager.link_certificate.assert_called_once_with(
            cert_type=ssl_certificate_manager.certificate.ssl_type.value,
            domain=ssl_certificate_manager.certificate.domain,
            privkey_path=expected_paths[0],
            fullchain_path=expected_paths[1],
            alias_domains=None,
        )

        # Verify nginx was restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_generate_certificate_with_alias_domains(self, ssl_certificate_manager):
        """Test that generate_certificate handles alias domains correctly."""
        expected_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        ssl_certificate_manager.service.generate_certificate.return_value = expected_paths

        alias_domains = ["www.example.com", "api.example.com"]
        ssl_certificate_manager.generate_certificate(alias_domains=alias_domains)

        # Verify alias domains were passed to service
        ssl_certificate_manager.service.generate_certificate.assert_called_once_with(
            ssl_certificate_manager.certificate, alias_domains
        )

        # Verify alias domains were passed to link_manager
        ssl_certificate_manager.link_manager.link_certificate.assert_called_once_with(
            cert_type=ssl_certificate_manager.certificate.ssl_type.value,
            domain=ssl_certificate_manager.certificate.domain,
            privkey_path=expected_paths[0],
            fullchain_path=expected_paths[1],
            alias_domains=alias_domains,
        )


class TestSSLCertificateManagerRenewCertificate:
    """Tests for SSLCertificateManager.renew_certificate method."""

    def test_renew_certificate_raises_if_not_due_for_renewal(self, mocker, ssl_certificate_manager):
        """Test that renew_certificate raises error if certificate doesn't need renewal."""
        # Mock needs_renewal to return False
        mocker.patch.object(ssl_certificate_manager, 'needs_renewal', return_value=False)
        expiry_date = datetime.now() + timedelta(days=60)
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_expiry', return_value=expiry_date)

        with pytest.raises(SSLCertificateNotDueForRenewalError):
            ssl_certificate_manager.renew_certificate()

    def test_renew_certificate_calls_service_and_updates_symlinks(self, mocker, ssl_certificate_manager):
        """Test that renew_certificate orchestrates renewal operations."""
        # Mock needs_renewal to return True
        mocker.patch.object(ssl_certificate_manager, 'needs_renewal', return_value=True)

        # Mock get_certificate_paths to return paths
        expected_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_paths', return_value=expected_paths)

        ssl_certificate_manager.renew_certificate()

        # Verify service.renew_certificate was called
        ssl_certificate_manager.service.renew_certificate.assert_called_once_with(ssl_certificate_manager.certificate)

        # Verify symlinks were recreated
        ssl_certificate_manager.link_manager.link_certificate.assert_called_once()

        # Verify nginx was restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_renew_certificate_with_alias_domains(self, mocker, ssl_certificate_manager):
        """Test that renew_certificate handles alias domains correctly."""
        mocker.patch.object(ssl_certificate_manager, 'needs_renewal', return_value=True)
        expected_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_paths', return_value=expected_paths)

        alias_domains = ["www.example.com", "api.example.com"]
        ssl_certificate_manager.renew_certificate(alias_domains=alias_domains)

        # Verify alias domains were passed to link_manager
        ssl_certificate_manager.link_manager.link_certificate.assert_called_once_with(
            cert_type=ssl_certificate_manager.certificate.ssl_type.value,
            domain=ssl_certificate_manager.certificate.domain,
            privkey_path=expected_paths[0],
            fullchain_path=expected_paths[1],
            alias_domains=alias_domains,
        )

    def test_renew_certificate_continues_if_symlink_recreation_fails(self, mocker, ssl_certificate_manager):
        """Test that renew_certificate continues even if symlink recreation fails."""
        mocker.patch.object(ssl_certificate_manager, 'needs_renewal', return_value=True)

        # Mock get_certificate_paths to raise an exception
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_paths', side_effect=Exception("Symlink error"))

        # Should not raise - renewal succeeded even if symlink recreation failed
        ssl_certificate_manager.renew_certificate()

        # Verify service.renew_certificate was still called
        ssl_certificate_manager.service.renew_certificate.assert_called_once()

        # Verify nginx was still restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()


class TestSSLCertificateManagerRemoveCertificate:
    """Tests for SSLCertificateManager.remove_certificate method."""

    def test_remove_certificate_unlinks_and_removes(self, ssl_certificate_manager):
        """Test that remove_certificate unlinks symlinks and removes certificate."""
        ssl_certificate_manager.remove_certificate()

        # Verify symlinks were removed first
        ssl_certificate_manager.link_manager.unlink_certificate.assert_called_once_with(
            ssl_certificate_manager.certificate.domain, None
        )

        # Verify certificate was removed from service
        ssl_certificate_manager.service.remove_certificate.assert_called_once_with(ssl_certificate_manager.certificate)

        # Verify nginx was restarted
        ssl_certificate_manager.nginx_controller.restart.assert_called_once()

    def test_remove_certificate_with_alias_domains(self, ssl_certificate_manager):
        """Test that remove_certificate handles alias domains correctly."""
        alias_domains = ["www.example.com", "api.example.com"]
        ssl_certificate_manager.remove_certificate(alias_domains=alias_domains)

        # Verify alias domains were passed to unlink_certificate
        ssl_certificate_manager.link_manager.unlink_certificate.assert_called_once_with(
            ssl_certificate_manager.certificate.domain, alias_domains
        )

    def test_remove_certificate_operation_order(self, ssl_certificate_manager):
        """Test that remove_certificate operations happen in correct order."""
        call_order = []

        # Track call order
        ssl_certificate_manager.link_manager.unlink_certificate.side_effect = lambda *args, **kwargs: call_order.append(
            'unlink'
        )
        ssl_certificate_manager.service.remove_certificate.side_effect = lambda *args, **kwargs: call_order.append(
            'remove'
        )
        ssl_certificate_manager.nginx_controller.restart.side_effect = lambda *args, **kwargs: call_order.append(
            'restart'
        )

        ssl_certificate_manager.remove_certificate()

        # Verify correct order: unlink, then remove, then restart
        assert call_order == ['unlink', 'remove', 'restart']


class TestSSLCertificateManagerIntegration:
    """Integration tests for SSLCertificateManager workflow scenarios."""

    def test_full_certificate_lifecycle(self, mocker, ssl_certificate_manager):
        """Test complete certificate lifecycle: generate, renew, remove."""
        # Setup mocks for generate
        generate_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        ssl_certificate_manager.service.generate_certificate.return_value = generate_paths

        # Step 1: Generate certificate
        ssl_certificate_manager.generate_certificate()

        assert ssl_certificate_manager.service.generate_certificate.called
        assert ssl_certificate_manager.link_manager.link_certificate.called
        assert ssl_certificate_manager.nginx_controller.restart.called

        # Reset mocks
        ssl_certificate_manager.service.reset_mock()
        ssl_certificate_manager.link_manager.reset_mock()
        ssl_certificate_manager.nginx_controller.reset_mock()

        # Setup mocks for renew
        mocker.patch.object(ssl_certificate_manager, 'needs_renewal', return_value=True)
        mocker.patch.object(ssl_certificate_manager, 'get_certificate_paths', return_value=generate_paths)

        # Step 2: Renew certificate
        ssl_certificate_manager.renew_certificate()

        assert ssl_certificate_manager.service.renew_certificate.called
        assert ssl_certificate_manager.nginx_controller.restart.called

        # Reset mocks
        ssl_certificate_manager.service.reset_mock()
        ssl_certificate_manager.link_manager.reset_mock()
        ssl_certificate_manager.nginx_controller.reset_mock()

        # Step 3: Remove certificate
        ssl_certificate_manager.remove_certificate()

        assert ssl_certificate_manager.link_manager.unlink_certificate.called
        assert ssl_certificate_manager.service.remove_certificate.called
        assert ssl_certificate_manager.nginx_controller.restart.called

    def test_generate_with_alias_domains_full_flow(self, ssl_certificate_manager):
        """Test generating certificate with alias domains handles all operations."""
        generate_paths = (Path("/privkey.pem"), Path("/fullchain.pem"))
        ssl_certificate_manager.service.generate_certificate.return_value = generate_paths

        alias_domains = ["www.example.com", "api.example.com", "admin.example.com"]

        ssl_certificate_manager.generate_certificate(alias_domains=alias_domains)

        # Verify service received alias domains
        call_args = ssl_certificate_manager.service.generate_certificate.call_args
        assert call_args[0][1] == alias_domains

        # Verify link_manager received alias domains
        call_args = ssl_certificate_manager.link_manager.link_certificate.call_args
        assert call_args[1]['alias_domains'] == alias_domains
