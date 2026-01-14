"""
Tests for frappe_manager.ssl_manager.acmesh_certificate_service module.

This module tests the AcmeShCertificateService class which handles certificate
operations using the acme.sh client.
"""

import os
import pytest
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call
from frappe_manager.ssl_manager.acmesh_certificate_service import AcmeShCertificateService
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import CustomDomainCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateGenerateFailed,
    SSLCertificateNotFoundError,
)
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE


class TestAcmeShCertificateServiceInitialization:
    """Tests for AcmeShCertificateService initialization."""

    def test_init_stores_paths_and_creates_root_dir(self, tmp_path, mock_output_handler):
        """Test that initialization stores paths and creates root directory."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        with patch.object(AcmeShCertificateService, '_ensure_acmesh_installed'):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            assert service.webroot_dir == webroot_dir
            assert service.root_dir == ssl_dir / "acmesh"
            assert service.acmesh_home == ssl_dir / "acmesh" / ".acme.sh"
            assert service.acmesh_bin == ssl_dir / "acmesh" / ".acme.sh" / "acme.sh"
            assert service.output == mock_output_handler
            assert service.root_dir.exists()

    def test_init_with_custom_acmesh_home(self, tmp_path, mock_output_handler):
        """Test initialization with custom acmesh_home path."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        custom_home = tmp_path / "custom_acmesh"
        webroot_dir.mkdir(parents=True)

        with patch.object(AcmeShCertificateService, '_ensure_acmesh_installed'):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                acmesh_home=custom_home,
                output_handler=mock_output_handler,
            )

            assert service.acmesh_home == custom_home
            assert service.acmesh_bin == custom_home / "acme.sh"

    def test_init_calls_ensure_acmesh_installed(self, tmp_path, mock_output_handler):
        """Test that initialization calls _ensure_acmesh_installed."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        with patch.object(AcmeShCertificateService, '_ensure_acmesh_installed') as mock_ensure:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            mock_ensure.assert_called_once()


class TestAcmeShCertificateServiceEnsureInstalled:
    """Tests for acme.sh installation logic."""

    def test_ensure_installed_skips_if_binary_exists(self, tmp_path, mock_output_handler):
        """Test that installation is skipped if binary already exists."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        # Create acme.sh binary
        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        acmesh_bin = acmesh_home / "acme.sh"
        acmesh_bin.touch()

        # Reset class-level cache
        AcmeShCertificateService._acmesh_installed = False

        with patch('subprocess.run') as mock_run:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            # Should not call subprocess.run for installation
            mock_run.assert_not_called()
            mock_output_handler.debug.assert_called_with(f"acme.sh found at {acmesh_bin}")

    def test_ensure_installed_installs_if_missing(self, tmp_path, mock_output_handler):
        """Test that acme.sh is installed if missing."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        # Reset class-level cache
        AcmeShCertificateService._acmesh_installed = False

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"

        # Mock successful installation
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Installation successful"
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            # Create the binary after "installation"
            def create_binary(*args, **kwargs):
                acmesh_home.mkdir(parents=True)
                (acmesh_home / "acme.sh").touch()
                return mock_result

            mock_run.side_effect = create_binary

            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            # Should have called subprocess for installation
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert "curl -s https://get.acme.sh" in call_args[0][0]
            assert f"--home {acmesh_home}" in call_args[0][0]
            assert call_args[1]['shell'] is True

            mock_output_handler.change_head.assert_called_with("Installing acme.sh")
            mock_output_handler.print.assert_any_call("acme.sh installed successfully")

    def test_ensure_installed_raises_on_failure(self, tmp_path, mock_output_handler):
        """Test that installation failure raises exception."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        # Reset class-level cache
        AcmeShCertificateService._acmesh_installed = False

        # Mock failed installation
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'curl', stderr="Network error")):
            with pytest.raises(Exception, match="Failed to install acme.sh"):
                service = AcmeShCertificateService(
                    ssl_service_dir=ssl_dir,
                    webroot_dir=webroot_dir,
                    output_handler=mock_output_handler,
                )


class TestAcmeShCertificateServiceRunCommand:
    """Tests for _run_acmesh_command method."""

    def test_run_command_executes_with_correct_env(self, tmp_path, mock_output_handler):
        """Test that acme.sh commands are executed with correct environment."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        # Create acme.sh binary
        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        acmesh_bin = acmesh_home / "acme.sh"
        acmesh_bin.touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Success"
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service._run_acmesh_command(["--version"], env={"CUSTOM_VAR": "value"})

            assert result == mock_result
            call_args = mock_run.call_args
            assert call_args[0][0][0] == str(acmesh_bin)
            assert call_args[0][0][1] == "--version"
            assert call_args[1]['env']['LE_WORKING_DIR'] == str(acmesh_home)
            assert call_args[1]['env']['CUSTOM_VAR'] == "value"
            assert call_args[1]['capture_output'] is True
            assert call_args[1]['text'] is True

    def test_run_command_logs_failure(self, tmp_path, mock_output_handler):
        """Test that command failures are logged."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        acmesh_bin = acmesh_home / "acme.sh"
        acmesh_bin.touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = "Some output"
        mock_result.stderr = "Error message"

        with patch('subprocess.run', return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service._run_acmesh_command(["--fail"])

            assert result.returncode == 1
            mock_output_handler.debug.assert_any_call("acme.sh failed: Error message")


class TestAcmeShCertificateServiceGenerateCertificate:
    """Tests for generate_certificate method."""

    def test_generate_certificate_http01_success(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test successful certificate generation with HTTP-01 challenge."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        # Setup acme.sh environment
        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        # Create certificate files that acme.sh would create
        cert_dir = acmesh_home / "example.com_ecc"
        cert_dir.mkdir(parents=True)
        (cert_dir / "example.com.key").write_text("key content")
        (cert_dir / "fullchain.cer").write_text("cert content")

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Certificate issued"
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            privkey_path, fullchain_path = service.generate_certificate(mock_http_certificate)

            # Verify returned paths
            assert privkey_path == ssl_dir / "acmesh" / "example.com" / "key.pem"
            assert fullchain_path == ssl_dir / "acmesh" / "example.com" / "fullchain.pem"
            assert privkey_path.exists()
            assert fullchain_path.exists()

    def test_generate_certificate_uses_staging_flag(
        self, tmp_path, mock_output_handler, mock_http_certificate, monkeypatch
    ):
        """Test that staging flag is used when environment variable is set."""
        monkeypatch.setenv("FM_LETSENCRYPT_STAGING", "1")

        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        cert_dir = acmesh_home / "example.com_ecc"
        cert_dir.mkdir(parents=True)
        (cert_dir / "example.com.key").write_text("key")
        (cert_dir / "fullchain.cer").write_text("cert")

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            service.generate_certificate(mock_http_certificate)

            # Check that --staging was in the command
            call_args = mock_run.call_args_list[-1]  # Last call (actual cert generation)
            command = call_args[0][0]
            assert "--staging" in command

    def test_generate_certificate_with_email(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test that email is passed to acme.sh."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        cert_dir = acmesh_home / "example.com_ecc"
        cert_dir.mkdir(parents=True)
        (cert_dir / "example.com.key").write_text("key")
        (cert_dir / "fullchain.cer").write_text("cert")

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        # Add email to certificate
        mock_http_certificate.email = "test@example.com"

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            service.generate_certificate(mock_http_certificate)

            # Check that email was in the command
            call_args = mock_run.call_args_list[-1]
            command = call_args[0][0]
            assert "--accountemail" in command
            assert "test@example.com" in command

    def test_generate_certificate_raises_on_failure(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test that certificate generation failure raises exception."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Certificate issuance failed"

        with patch('subprocess.run', return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            with pytest.raises(SSLCertificateGenerateFailed):
                service.generate_certificate(mock_http_certificate)

    def test_generate_certificate_raises_if_files_not_found(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test that missing certificate files raise exception."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        # Don't create certificate files - simulate acme.sh success but missing output

        with patch('subprocess.run', return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            with pytest.raises(SSLCertificateNotFoundError):
                service.generate_certificate(mock_http_certificate)


class TestAcmeShCertificateServiceRenewCertificate:
    """Tests for renew_certificate method."""

    def test_renew_certificate_success(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test successful certificate renewal."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        # Create existing certificate directory
        dest_dir = ssl_dir / "acmesh" / "example.com"
        dest_dir.mkdir(parents=True)

        # Create renewed certificate files in acme.sh home
        cert_dir = acmesh_home / "example.com_ecc"
        cert_dir.mkdir(parents=True)
        (cert_dir / "example.com.key").write_text("renewed key")
        (cert_dir / "fullchain.cer").write_text("renewed cert")

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service.renew_certificate(mock_http_certificate)

            assert result is True
            # Check that renewed files were copied
            assert (dest_dir / "key.pem").exists()
            assert (dest_dir / "fullchain.pem").exists()

            # Verify --renew was in command
            call_args = mock_run.call_args_list[-1]
            command = call_args[0][0]
            assert "--renew" in command
            assert "example.com" in command

    def test_renew_certificate_failure(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test failed certificate renewal."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Renewal failed"

        with patch('subprocess.run', return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service.renew_certificate(mock_http_certificate)

            assert result is False


class TestAcmeShCertificateServiceRemoveCertificate:
    """Tests for remove_certificate method."""

    def test_remove_certificate_success(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test successful certificate removal."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        # Create certificate directory to remove
        cert_dir = ssl_dir / "acmesh" / "example.com"
        cert_dir.mkdir(parents=True)
        (cert_dir / "key.pem").touch()
        (cert_dir / "fullchain.pem").touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service.remove_certificate(mock_http_certificate)

            assert result is True
            assert not cert_dir.exists()

            # Verify --remove was in command
            call_args = mock_run.call_args_list[-1]
            command = call_args[0][0]
            assert "--remove" in command
            assert "example.com" in command

    def test_remove_certificate_failure(self, tmp_path, mock_output_handler, mock_http_certificate):
        """Test failed certificate removal."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Removal failed"

        with patch('subprocess.run', return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service.remove_certificate(mock_http_certificate)

            assert result is False

    def test_remove_certificate_removes_directory_even_on_acmesh_failure(
        self, tmp_path, mock_output_handler, mock_http_certificate
    ):
        """Test that certificate directory is removed even if acme.sh fails."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        # Create certificate directory
        cert_dir = ssl_dir / "acmesh" / "example.com"
        cert_dir.mkdir(parents=True)
        (cert_dir / "key.pem").touch()

        AcmeShCertificateService._acmesh_installed = False

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            service.remove_certificate(mock_http_certificate)

            # Directory should still be removed
            assert not cert_dir.exists()
