"""
Tests for frappe_manager.ssl_manager.no_op_certificate_service module.

This module tests the NoOpCertificateService class which provides
no-op implementations for when SSL is disabled.
"""

from pathlib import Path

from frappe_manager.ssl_manager.no_op_certificate_service import NoOpCertificateService


class TestNoOpCertificateServiceInitialization:
    """Tests for NoOpCertificateService initialization."""

    def test_init_stores_root_dir_and_output_handler(self, tmp_path, mock_output_handler, mocker):
        """Test that initialization stores root directory and output handler."""
        root_dir = tmp_path / "ssl"
        mock_logger = mocker.MagicMock()

        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=root_dir,
            output_handler=mock_output_handler,
        )

        assert service.root_dir == root_dir
        assert service.output == mock_output_handler

    def test_init_with_defaults(self, mocker):
        """Test that initialization works with default values."""
        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(logger=mock_logger)

        assert service.root_dir == Path("/dev/null")
        assert service.output is not None


class TestNoOpCertificateServiceGenerateCertificate:
    """Tests for NoOpCertificateService.generate_certificate method."""

    def test_generate_certificate_returns_dev_null_paths(self, tmp_path, mock_output_handler, mock_certificate, mocker):
        """Test that generate_certificate returns /dev/null paths."""
        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=tmp_path / "ssl",
            output_handler=mock_output_handler,
        )

        result = service.generate_certificate(mock_certificate)

        assert result == (Path("/dev/null"), Path("/dev/null"))

    def test_generate_certificate_does_not_create_files(self, tmp_path, mock_output_handler, mock_certificate, mocker):
        """Test that generate_certificate doesn't create any files."""
        root_dir = tmp_path / "ssl"
        root_dir.mkdir()
        mock_logger = mocker.MagicMock()

        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=root_dir,
            output_handler=mock_output_handler,
        )

        service.generate_certificate(mock_certificate)

        # Verify no files were created
        assert not any(root_dir.iterdir())

    def test_generate_certificate_with_dry_run(self, tmp_path, mock_output_handler, mock_certificate, mocker):
        """Test that generate_certificate handles dry_run parameter."""
        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=tmp_path / "ssl",
            output_handler=mock_output_handler,
        )

        result = service.generate_certificate(mock_certificate, dry_run=True)

        assert result == (Path("/dev/null"), Path("/dev/null"))


class TestNoOpCertificateServiceRenewCertificate:
    """Tests for NoOpCertificateService.renew_certificate method."""

    def test_renew_certificate_returns_true(self, tmp_path, mock_output_handler, mock_certificate, mocker):
        """Test that renew_certificate returns True."""
        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=tmp_path / "ssl",
            output_handler=mock_output_handler,
        )

        result = service.renew_certificate(mock_certificate)

        assert result is True


class TestNoOpCertificateServiceRemoveCertificate:
    """Tests for NoOpCertificateService.remove_certificate method."""

    def test_remove_certificate_shows_warning(self, tmp_path, mock_output_handler, mock_certificate, mocker):
        """Test that remove_certificate shows a warning message."""
        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=tmp_path / "ssl",
            output_handler=mock_output_handler,
        )

        service.remove_certificate(mock_certificate)

        # Verify warning was called with domain
        mock_output_handler.warning.assert_called_once()
        call_args = mock_output_handler.warning.call_args[0][0]
        assert mock_certificate.domain in call_args
        assert "doesn't have certificate issued" in call_args

    def test_remove_certificate_does_not_delete_files(self, tmp_path, mock_output_handler, mock_certificate, mocker):
        """Test that remove_certificate doesn't delete any files."""
        root_dir = tmp_path / "ssl"
        root_dir.mkdir()

        # Create a dummy file
        dummy_file = root_dir / "test.txt"
        dummy_file.write_text("test")

        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=root_dir,
            output_handler=mock_output_handler,
        )

        service.remove_certificate(mock_certificate)

        # Verify file still exists
        assert dummy_file.exists()


class TestNoOpCertificateServiceIntegration:
    """Integration tests for NoOpCertificateService behavior."""

    def test_all_methods_are_safe_to_call(self, tmp_path, mock_output_handler, mock_certificate, mocker):
        """Test that all methods can be called without side effects."""
        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=tmp_path / "ssl",
            output_handler=mock_output_handler,
        )

        # All operations should succeed without errors
        gen_result = service.generate_certificate(mock_certificate)
        renew_result = service.renew_certificate(mock_certificate)
        service.remove_certificate(mock_certificate)

        assert gen_result == (Path("/dev/null"), Path("/dev/null"))
        assert renew_result is True

    def test_service_works_with_letsencrypt_certificate(self, tmp_path, mock_output_handler, mock_letsencrypt_certificate_http01, mocker):
        """Test that service works with Let's Encrypt certificate types."""
        mock_logger = mocker.MagicMock()
        service = NoOpCertificateService(
            logger=mock_logger,
            root_dir=tmp_path / "ssl",
            output_handler=mock_output_handler,
        )

        # Should handle Let's Encrypt certs without issues
        result = service.generate_certificate(mock_letsencrypt_certificate_http01)

        assert result == (Path("/dev/null"), Path("/dev/null"))
