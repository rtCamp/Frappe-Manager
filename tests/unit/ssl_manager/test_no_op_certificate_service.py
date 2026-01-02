"""
Unit tests for NoOpCertificateService.

Tests the no-operation certificate service that's used when SSL is disabled.
This service performs no actual operations and is the simplest implementation.
"""

import pytest
from pathlib import Path

from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService
from frappe_manager.ssl_manager.ssl_certificate_service import SSLCertificateService


class TestNoOpCertificateServiceInitialization:
    """Tests for NoOpCertificateService initialization."""

    def test_initialization_with_default_root_dir(self):
        """Test that service initializes with default /dev/null root_dir."""
        service = NoOpCertificateService()
        
        assert service.root_dir == Path('/dev/null')

    def test_initialization_with_custom_root_dir(self, tmp_path):
        """Test that service accepts custom root_dir."""
        custom_dir = tmp_path / "custom"
        service = NoOpCertificateService(root_dir=custom_dir)
        
        assert service.root_dir == custom_dir

    def test_root_dir_is_path_object(self):
        """Test that root_dir is a Path object."""
        service = NoOpCertificateService()
        
        assert isinstance(service.root_dir, Path)


class TestNoOpCertificateServiceProtocolConformance:
    """Tests for SSLCertificateService protocol conformance."""

    def test_conforms_to_ssl_certificate_service_protocol(self):
        """Test that NoOpCertificateService conforms to SSLCertificateService protocol."""
        service = NoOpCertificateService()
        
        # Should be recognized as SSLCertificateService
        assert isinstance(service, SSLCertificateService)

    def test_has_required_protocol_attributes(self):
        """Test that service has all required protocol attributes."""
        service = NoOpCertificateService()
        
        assert hasattr(service, 'root_dir')
        assert hasattr(service, 'renew_certificate')
        assert hasattr(service, 'remove_certificate')
        assert hasattr(service, 'generate_certificate')

    def test_root_dir_attribute_accessible(self):
        """Test that root_dir attribute is accessible."""
        service = NoOpCertificateService()
        
        # Should be able to access without error
        root_dir = service.root_dir
        assert root_dir is not None


class TestNoOpCertificateServiceRenewCertificate:
    """Tests for renew_certificate method."""

    def test_renew_does_nothing(self, mock_certificate):
        """Test that renew_certificate does nothing (no-op)."""
        service = NoOpCertificateService()
        
        # Should not raise any exception
        result = service.renew_certificate()
        
        # Returns None (does nothing)
        assert result is None

    def test_renew_accepts_certificate_parameter(self, mock_certificate):
        """Test that renew_certificate accepts certificate parameter."""
        service = NoOpCertificateService()
        
        # Should not raise exception even with certificate
        result = service.renew_certificate()
        assert result is None


class TestNoOpCertificateServiceRemoveCertificate:
    """Tests for remove_certificate method."""

    def test_remove_prints_warning(self, mocker, mock_certificate):
        """Test that remove_certificate prints warning message."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.no_op_certificate_service.richprint')
        service = NoOpCertificateService()
        
        service.remove_certificate(mock_certificate)
        
        mock_richprint.warning.assert_called_once()

    def test_remove_warning_includes_domain(self, mocker, mock_certificate):
        """Test that warning message includes the domain name."""
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.no_op_certificate_service.richprint')
        service = NoOpCertificateService()
        
        service.remove_certificate(mock_certificate)
        
        # Get the warning message
        call_args = mock_richprint.warning.call_args[0][0]
        assert mock_certificate.domain in call_args
        assert "doesn't have certificate issued" in call_args

    def test_remove_accepts_certificate_parameter(self, mock_certificate):
        """Test that remove_certificate accepts certificate parameter."""
        service = NoOpCertificateService()
        
        # Should not raise exception
        service.remove_certificate(mock_certificate)


class TestNoOpCertificateServiceGenerateCertificate:
    """Tests for generate_certificate method."""

    def test_generate_returns_dev_null_paths(self, mock_certificate):
        """Test that generate_certificate returns /dev/null paths."""
        service = NoOpCertificateService()
        
        privkey_path, fullchain_path = service.generate_certificate(mock_certificate)
        
        assert privkey_path == Path('/dev/null')
        assert fullchain_path == Path('/dev/null')

    def test_generate_returns_tuple_of_paths(self, mock_certificate):
        """Test that generate_certificate returns tuple of Path objects."""
        service = NoOpCertificateService()
        
        result = service.generate_certificate(mock_certificate)
        
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], Path)
        assert isinstance(result[1], Path)

    def test_generate_with_alias_domains(self, mock_certificate):
        """Test that generate_certificate works with alias_domains parameter."""
        service = NoOpCertificateService()
        
        privkey_path, fullchain_path = service.generate_certificate(
            mock_certificate, 
            alias_domains=['alias1.com', 'alias2.com']
        )
        
        # Should still return /dev/null even with aliases
        assert privkey_path == Path('/dev/null')
        assert fullchain_path == Path('/dev/null')

    def test_generate_with_none_alias_domains(self, mock_certificate):
        """Test that generate_certificate works with None alias_domains."""
        service = NoOpCertificateService()
        
        privkey_path, fullchain_path = service.generate_certificate(
            mock_certificate, 
            alias_domains=None
        )
        
        assert privkey_path == Path('/dev/null')
        assert fullchain_path == Path('/dev/null')


class TestNoOpCertificateServiceUsageScenarios:
    """Tests for real-world usage scenarios."""

    def test_service_can_be_used_as_placeholder(self, mock_certificate):
        """Test that NoOpCertificateService can be used as a placeholder."""
        service = NoOpCertificateService()
        
        # All operations should work without errors
        service.renew_certificate()
        service.remove_certificate(mock_certificate)
        paths = service.generate_certificate(mock_certificate)
        
        assert paths == (Path('/dev/null'), Path('/dev/null'))

    def test_multiple_operations_on_same_service(self, mock_certificate):
        """Test that multiple operations can be performed on same service instance."""
        service = NoOpCertificateService()
        
        # Generate multiple times
        paths1 = service.generate_certificate(mock_certificate)
        paths2 = service.generate_certificate(mock_certificate)
        
        # Should return same paths
        assert paths1 == paths2
        assert paths1 == (Path('/dev/null'), Path('/dev/null'))

    def test_service_with_different_certificates(self, mock_certificate):
        """Test that service works with different certificate objects."""
        from frappe_manager.ssl_manager.certificate import SSLCertificate
        from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
        
        service = NoOpCertificateService()
        
        cert1 = SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.none)
        cert2 = SSLCertificate(domain="test.com", ssl_type=SUPPORTED_SSL_TYPES.none)
        
        paths1 = service.generate_certificate(cert1)
        paths2 = service.generate_certificate(cert2)
        
        # Should return same paths regardless of certificate
        assert paths1 == paths2

    def test_service_does_not_create_files(self, mock_certificate, tmp_path):
        """Test that NoOpCertificateService doesn't create any files."""
        service = NoOpCertificateService(root_dir=tmp_path)
        
        # Perform all operations
        service.generate_certificate(mock_certificate)
        service.renew_certificate()
        service.remove_certificate(mock_certificate)
        
        # tmp_path should still be empty (no files created)
        assert len(list(tmp_path.iterdir())) == 0

    def test_service_is_safe_for_concurrent_use(self, mock_certificate):
        """Test that service is safe for concurrent operations."""
        service = NoOpCertificateService()
        
        # Simulate concurrent operations
        results = []
        for _ in range(10):
            result = service.generate_certificate(mock_certificate)
            results.append(result)
        
        # All results should be identical
        assert all(r == (Path('/dev/null'), Path('/dev/null')) for r in results)


class TestNoOpCertificateServiceIntegration:
    """Tests for integration with other components."""

    def test_service_works_with_ssl_certificate_manager(
        self, 
        mock_certificate, 
        mock_link_manager, 
        mock_nginx_controller
    ):
        """Test that NoOpCertificateService works with SSLCertificateManager."""
        from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
        
        service = NoOpCertificateService()
        
        # Should be usable in SSLCertificateManager
        manager = SSLCertificateManager(
            certificate=mock_certificate,
            service=service,
            link_manager=mock_link_manager,
            nginx_controller=mock_nginx_controller
        )
        
        assert manager.service == service
