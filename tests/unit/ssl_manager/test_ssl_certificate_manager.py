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
        self,
        mock_certificate,
        mock_ssl_service,
        mock_storage_config,
        mock_link_manager,
        mock_nginx_controller,
        mock_output_handler,
    ):
        """Test that initialization stores all dependencies."""

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        manager = SSLCertificateManager(
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
        self, mock_ssl_service, mock_storage_config, mock_link_manager, mock_nginx_controller, mock_output_handler
    ):
        """Test that initialization works with empty certificate list."""

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        manager = SSLCertificateManager(
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
        self, mock_certificate, mock_ssl_service, mock_storage_config, mock_nginx_controller, mock_output_handler
    ):
        """Test that initialization raises ValueError if link_manager is None."""

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        with pytest.raises(ValueError, match="Certificate link manager is required"):
            SSLCertificateManager(
                certificates=[mock_certificate],
                service_factory=service_factory,
                link_manager=None,
                nginx_controller=mock_nginx_controller,
                storage_config=mock_storage_config,
                output_handler=mock_output_handler,
            )

    def test_init_raises_if_nginx_controller_is_none(
        self, mock_certificate, mock_ssl_service, mock_storage_config, mock_link_manager, mock_output_handler
    ):
        """Test that initialization raises ValueError if nginx_controller is None."""

        def service_factory(cert, storage_cfg, output_handler):
            return mock_ssl_service

        with pytest.raises(ValueError, match="Nginx controller is required"):
            SSLCertificateManager(
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

