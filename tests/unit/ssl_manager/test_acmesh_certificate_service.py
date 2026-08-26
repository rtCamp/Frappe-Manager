"""
Tests for frappe_manager.ssl_manager.acmesh_certificate_service module.

This module tests the AcmeShCertificateService class which handles certificate
operations using the acme.sh client.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.site_manager.bench_config import FMBenchEnvType
from frappe_manager.ssl_manager.acmesh_certificate_service import (
    LETSENCRYPT_PRODUCTION_SERVER,
    LETSENCRYPT_STAGING_SERVER,
    AcmeShCertificateService,
)
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateGenerateFailed,
    SSLCertificateNotFoundError,
)


def _no_global_dns_credentials():
    """Pin the global scope empty, so only the bench scope can satisfy a DNS-01 run.

    Without this the developer's own ~/frappe/fm_config.toml decides the result and the test passes
    for the wrong reason.
    """
    stub = SimpleNamespace(dns_providers={})
    return patch.object(FMConfigManager, "import_from_toml", staticmethod(lambda *a, **k: stub))


class TestAcmeShCertificateServiceInitialization:
    """Tests for AcmeShCertificateService initialization."""

    def test_init_stores_paths_and_creates_root_dir(self, mock_logger, mocker, tmp_path, mock_output_handler):
        """Test that initialization stores paths and creates root directory."""

        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        with patch.object(AcmeShCertificateService, "_ensure_acmesh_installed"):
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

    def test_init_with_custom_acmesh_home(self, mock_logger, tmp_path, mock_output_handler):
        """Test initialization with custom acmesh_home path."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        custom_home = tmp_path / "custom_acmesh"
        webroot_dir.mkdir(parents=True)

        with patch.object(AcmeShCertificateService, "_ensure_acmesh_installed"):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                acmesh_home=custom_home,
                output_handler=mock_output_handler,
            )

            assert service.acmesh_home == custom_home
            assert service.acmesh_bin == custom_home / "acme.sh"

    def test_init_calls_ensure_acmesh_installed(self, mock_logger, tmp_path, mock_output_handler):
        """Test that initialization calls _ensure_acmesh_installed."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        with patch.object(AcmeShCertificateService, "_ensure_acmesh_installed") as mock_ensure:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            mock_ensure.assert_called_once()


class TestAcmeShCertificateServiceEnsureInstalled:
    """Tests for acme.sh installation logic."""

    def test_ensure_installed_skips_if_binary_exists(self, mock_logger, tmp_path, mock_output_handler):
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

        with patch("subprocess.run") as mock_run:
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            # Should not call subprocess.run for installation
            mock_run.assert_not_called()
            mock_output_handler.debug.assert_called_with(f"acme.sh found at {acmesh_bin}")

    def test_ensure_installed_installs_if_missing(self, mock_logger, tmp_path, mock_output_handler):
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

        with patch("subprocess.run", return_value=mock_result) as mock_run:
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
            assert call_args[1]["shell"] is True

            mock_output_handler.change_head.assert_called_with("Installing acme.sh")
            mock_output_handler.print.assert_any_call("acme.sh installed successfully")

    def test_ensure_installed_raises_on_failure(self, mock_logger, tmp_path, mock_output_handler):
        """Test that installation failure raises exception."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        # Reset class-level cache
        AcmeShCertificateService._acmesh_installed = False

        # Mock failed installation
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "curl", stderr="Network error")):
            with pytest.raises(Exception, match="Failed to install acme.sh"):
                service = AcmeShCertificateService(
                    ssl_service_dir=ssl_dir,
                    webroot_dir=webroot_dir,
                    output_handler=mock_output_handler,
                )


class TestAcmeShCertificateServiceRunCommand:
    """Tests for _run_acmesh_command method."""

    def test_run_command_executes_with_correct_env(self, mock_logger, tmp_path, mock_output_handler):
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

        with patch("subprocess.run", return_value=mock_result) as mock_run:
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
            assert call_args[1]["env"]["LE_WORKING_DIR"] == str(acmesh_home)
            assert call_args[1]["env"]["CUSTOM_VAR"] == "value"
            assert call_args[1]["capture_output"] is True
            assert call_args[1]["text"] is True

    def test_run_command_logs_failure(self, mock_logger, tmp_path, mock_output_handler):
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

        with patch("subprocess.run", return_value=mock_result):
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

    def test_generate_certificate_http01_success(
        self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate
    ):
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

        with patch("subprocess.run", return_value=mock_result):
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
        self,
        mock_logger,
        tmp_path,
        mock_output_handler,
        mock_http_certificate,
        monkeypatch,
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

        captured_command = []

        def mock_stream_output(cmd, env, cwd):
            # Capture the command for inspection
            captured_command.append(cmd)
            yield ("exit_code", b"0")

        # Make mock_output_handler.live_lines consume the generator
        def consume_generator(gen, **kwargs):
            for _ in gen:
                pass

        mock_output_handler.live_lines.side_effect = consume_generator

        with patch(
            "frappe_manager.ssl_manager.acmesh_certificate_service.stream_command_output",
            side_effect=mock_stream_output,
        ):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            service.generate_certificate(mock_http_certificate)

            assert len(captured_command) > 0
            command = captured_command[0]
            assert "https://acme-staging-v02.api.letsencrypt.org/directory" in " ".join(command)

    def test_generate_certificate_raises_on_failure(
        self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate
    ):
        """Test that certificate generation failure raises exception."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        AcmeShCertificateService._acmesh_installed = False

        def mock_stream_output(cmd, env, cwd):
            yield ("stderr", b"Certificate issuance failed")
            yield ("exit_code", b"1")

        def consume_generator(gen, **kwargs):
            for _ in gen:
                pass

        mock_output_handler.live_lines.side_effect = consume_generator

        with patch(
            "frappe_manager.ssl_manager.acmesh_certificate_service.stream_command_output",
            side_effect=mock_stream_output,
        ):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            with pytest.raises(SSLCertificateGenerateFailed):
                service.generate_certificate(mock_http_certificate)

    def test_generate_certificate_raises_if_files_not_found(
        self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate
    ):
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

        with patch("subprocess.run", return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            with pytest.raises(SSLCertificateNotFoundError):
                service.generate_certificate(mock_http_certificate)


class TestAcmeShCertificateServiceRenewCertificate:
    """Tests for renew_certificate method."""

    def test_renew_certificate_success(self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate):
        """Test successful certificate renewal."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()

        dest_dir = ssl_dir / "acmesh" / "example.com"
        dest_dir.mkdir(parents=True)

        cert_dir = acmesh_home / "example.com_ecc"
        cert_dir.mkdir(parents=True)
        (cert_dir / "example.com.key").write_text("renewed key")
        (cert_dir / "fullchain.cer").write_text("renewed cert")

        AcmeShCertificateService._acmesh_installed = False

        captured_command = []

        def mock_stream_output(cmd, env, cwd):
            captured_command.append(cmd)
            yield ("exit_code", b"0")

        def consume_generator(gen, **kwargs):
            for _ in gen:
                pass

        mock_output_handler.live_lines.side_effect = consume_generator

        with patch(
            "frappe_manager.ssl_manager.acmesh_certificate_service.stream_command_output",
            side_effect=mock_stream_output,
        ):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service.renew_certificate(mock_http_certificate)

            assert result is True
            assert (dest_dir / "key.pem").exists()
            assert (dest_dir / "fullchain.pem").exists()

            assert len(captured_command) > 0
            command = captured_command[0]
            assert "--renew" in " ".join(command)
            assert "example.com" in " ".join(command)

    def test_renew_certificate_failure(self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate):
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

        with patch("subprocess.run", return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service.renew_certificate(mock_http_certificate)

            assert result is False


class TestAcmeShCertificateServiceRemoveCertificate:
    """Tests for remove_certificate method."""

    def test_remove_certificate_success(self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate):
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

        with patch("subprocess.run", return_value=mock_result) as mock_run:
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

    def test_remove_certificate_failure(self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate):
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

        with patch("subprocess.run", return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            result = service.remove_certificate(mock_http_certificate)

            assert result is False

    def test_remove_certificate_removes_directory_even_on_acmesh_failure(
        self,
        mock_logger,
        tmp_path,
        mock_output_handler,
        mock_http_certificate,
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

        with patch("subprocess.run", return_value=mock_result):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            service.remove_certificate(mock_http_certificate)

            assert not cert_dir.exists()


class TestAcmeShCertificateServiceCredentialCache:
    """Tests for DNS credential cache clearing."""

    def test_clear_cached_dns_credentials_removes_cloudflare_creds(self, mock_logger, tmp_path, mock_output_handler):
        """Test that cached Cloudflare credentials are removed from account.conf."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        acmesh_bin = acmesh_home / "acme.sh"
        acmesh_bin.touch()

        account_conf = acmesh_home / "account.conf"
        account_conf.write_text(
            "LOG_FILE='/path/to/log'\n"
            "LOG_LEVEL='1'\n"
            "SAVED_CF_Token='old_token_abc123'\n"
            "SAVED_CF_Account_ID='old_account_id'\n"
            "SAVED_CF_Key='old_api_key'\n"
            "SAVED_CF_Email='old@example.com'\n"
            "SAVED_CF_Zone_ID='old_zone_id'\n"
            "DEFAULT_ACME_SERVER='https://acme-v02.api.letsencrypt.org/directory'\n",
        )

        AcmeShCertificateService._acmesh_installed = False

        service = AcmeShCertificateService(
            ssl_service_dir=ssl_dir,
            webroot_dir=webroot_dir,
            output_handler=mock_output_handler,
        )

        service._clear_cached_dns_credentials()

        updated_content = account_conf.read_text()
        assert "SAVED_CF_Token=" not in updated_content
        assert "SAVED_CF_Account_ID=" not in updated_content
        assert "SAVED_CF_Key=" not in updated_content
        assert "SAVED_CF_Email=" not in updated_content
        assert "SAVED_CF_Zone_ID=" not in updated_content
        assert "LOG_FILE=" in updated_content
        assert "LOG_LEVEL=" in updated_content
        assert "DEFAULT_ACME_SERVER=" in updated_content

    def test_clear_cached_dns_credentials_handles_missing_file(self, mock_logger, tmp_path, mock_output_handler):
        """Test that missing account.conf is handled gracefully."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        acmesh_bin = acmesh_home / "acme.sh"
        acmesh_bin.touch()

        AcmeShCertificateService._acmesh_installed = False

        service = AcmeShCertificateService(
            ssl_service_dir=ssl_dir,
            webroot_dir=webroot_dir,
            output_handler=mock_output_handler,
        )

        service._clear_cached_dns_credentials()

        mock_output_handler.debug.assert_called_with("account.conf not found, skipping credential cache clear")

    def test_clear_cached_dns_credentials_handles_exceptions(self, mock_logger, tmp_path, mock_output_handler):
        """Test that exceptions during credential clearing are handled gracefully."""
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        acmesh_bin = acmesh_home / "acme.sh"
        acmesh_bin.touch()

        account_conf = acmesh_home / "account.conf"
        account_conf.write_text("SAVED_CF_Token='test'\n")

        AcmeShCertificateService._acmesh_installed = False

        service = AcmeShCertificateService(
            ssl_service_dir=ssl_dir,
            webroot_dir=webroot_dir,
            output_handler=mock_output_handler,
        )

        with patch.object(Path, "read_text", side_effect=PermissionError("Access denied")):
            service._clear_cached_dns_credentials()

        mock_output_handler.warning.assert_called_once()
        assert "Failed to clear cached credentials" in str(mock_output_handler.warning.call_args)

    def test_generate_certificate_dns01_clears_cache(
        self, mock_logger, tmp_path, mock_output_handler, mock_dns_certificate
    ):
        """Test that DNS-01 certificate generation clears credential cache."""
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

        account_conf = acmesh_home / "account.conf"
        account_conf.write_text("SAVED_CF_Token='old_token'\n")

        AcmeShCertificateService._acmesh_installed = False

        with patch("frappe_manager.ssl_manager.ssl_utils.get_dns_credentials_for_certificate") as mock_get_creds:
            mock_get_creds.return_value = {"CF_Token": "new_token"}

            with patch("frappe_manager.ssl_manager.acmesh_certificate_service.stream_command_output") as mock_stream:
                mock_stream.return_value = [("exit_code", b"0")]

                service = AcmeShCertificateService(
                    ssl_service_dir=ssl_dir,
                    webroot_dir=webroot_dir,
                    output_handler=mock_output_handler,
                )

                service.generate_certificate(mock_dns_certificate)

                updated_content = account_conf.read_text()
                assert "SAVED_CF_Token=" not in updated_content

    def test_renew_certificate_dns01_clears_cache(
        self, mock_logger, tmp_path, mock_output_handler, mock_dns_certificate
    ):
        """Test that DNS-01 certificate renewal clears credential cache."""
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

        account_conf = acmesh_home / "account.conf"
        account_conf.write_text("SAVED_CF_Token='old_token'\n")

        service_cert_dir = ssl_dir / "acmesh" / "example.com"
        service_cert_dir.mkdir(parents=True)

        AcmeShCertificateService._acmesh_installed = False

        with (
            patch(
                "frappe_manager.ssl_manager.ssl_utils.get_dns_credentials_for_certificate",
                return_value={"CF_Token": "resolved_token"},
            ),
            patch("frappe_manager.ssl_manager.acmesh_certificate_service.stream_command_output") as mock_stream,
        ):
            mock_stream.return_value = [("exit_code", b"0")]

            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            service.renew_certificate(mock_dns_certificate)

            updated_content = account_conf.read_text()
            assert "SAVED_CF_Token=" not in updated_content

    def test_renew_certificate_dns01_supplies_credentials_after_clearing_them(
        self, mock_logger, tmp_path, mock_output_handler, mock_dns_certificate
    ):
        """A DNS-01 renewal must put credentials back in the environment after wiping the cache.

        `renew_certificate` clears acme.sh's `SAVED_CF_*` values, which on a `--renew` are the only
        credential acme.sh has: the flags come from the per-domain conf it already stored, not from
        the fm command line. Clearing without re-supplying left `dns_cf` unable to authenticate, so
        every DNS-01 renewal failed and `SSLCertificateManager` swallowed it as "Certificate not
        found in acme.sh" and performed a full re-issue, spending Let's Encrypt duplicate-certificate
        allowance. The three existing renewal tests all use an HTTP-01 fixture, which needs no
        credentials, so nothing caught it.
        """
        ssl_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir(parents=True)

        acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
        acmesh_home.mkdir(parents=True)
        (acmesh_home / "acme.sh").touch()
        (acmesh_home / "account.conf").write_text("SAVED_CF_Token='stale_token'\n")

        cert_dir = acmesh_home / "example.com_ecc"
        cert_dir.mkdir(parents=True)
        (cert_dir / "example.com.key").write_text("key")
        (cert_dir / "fullchain.cer").write_text("cert")
        (ssl_dir / "acmesh" / "example.com").mkdir(parents=True)

        AcmeShCertificateService._acmesh_installed = False

        captured_env: dict[str, str] = {}

        def mock_stream_output(cmd, env, cwd):
            captured_env.update(env)
            yield ("exit_code", b"0")

        def consume_generator(gen, **kwargs):
            for _ in gen:
                pass

        mock_output_handler.live_lines.side_effect = consume_generator

        with (
            patch(
                "frappe_manager.ssl_manager.ssl_utils.get_dns_credentials_for_certificate",
                return_value={"CF_Token": "resolved_token"},
            ),
            patch(
                "frappe_manager.ssl_manager.acmesh_certificate_service.stream_command_output",
                side_effect=mock_stream_output,
            ),
        ):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
            )

            assert service.renew_certificate(mock_dns_certificate) is True

        assert "SAVED_CF_Token=" not in (acmesh_home / "account.conf").read_text()
        assert captured_env.get("CF_Token") == "resolved_token"

    def test_the_bench_dns_provider_tier_reaches_acme_sh(
        self, mock_logger, tmp_path, mock_output_handler, mock_dns_certificate
    ):
        """`fm ssl dns-config cloudflare <bench>` must produce a credential issuance can use.

        The service was built without a bench_config, so `get_dns_credentials_for_certificate` could
        only ever reach the global `[cloudflare]` tier: a bench-scoped credential was written to
        `[ssl.dns_challenge_providers]`, echoed by `--show`, and then unreachable. That made the
        documented per-bench override a write-only feature, and it is the tier the credential
        migration relocates a legacy cert-borne secret into, so it has to actually work.
        """
        from frappe_manager.site_manager.bench_config import BenchConfig
        from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig

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
        (ssl_dir / "acmesh" / "example.com").mkdir(parents=True)

        bench_config = BenchConfig(
            name="example.com",
            developer_mode=False,
            admin_tools=False,
            environment_type=FMBenchEnvType.prod,
            root_path=tmp_path / "bench_config.toml",
            dns_providers={"cloudflare": DNSProviderConfig(email=None, api_token="cf-bench-tier", api_key=None)},
        )

        AcmeShCertificateService._acmesh_installed = False
        captured_env: dict[str, str] = {}

        def mock_stream_output(cmd, env, cwd):
            captured_env.update(env)
            yield ("exit_code", b"0")

        def consume_generator(gen, **kwargs):
            for _ in gen:
                pass

        mock_output_handler.live_lines.side_effect = consume_generator

        with (
            _no_global_dns_credentials(),
            patch(
                "frappe_manager.ssl_manager.acmesh_certificate_service.stream_command_output",
                side_effect=mock_stream_output,
            ),
        ):
            service = AcmeShCertificateService(
                ssl_service_dir=ssl_dir,
                webroot_dir=webroot_dir,
                output_handler=mock_output_handler,
                bench_config=bench_config,
            )

            assert service.renew_certificate(mock_dns_certificate) is True

        assert captured_env.get("CF_Token") == "cf-bench-tier"


class TestAcmeShCertificateServiceLetsEncryptServer:
    """Tests for Let's Encrypt server hardcoding."""

    def test_generate_certificate_uses_letsencrypt_production_by_default(
        self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate
    ):
        """Test that production Let's Encrypt server is used by default."""
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

        service = AcmeShCertificateService(
            ssl_service_dir=ssl_dir,
            webroot_dir=webroot_dir,
            output_handler=mock_output_handler,
        )

        with patch.object(service, "_stream_acmesh_command", return_value=0) as mock_stream:
            service.generate_certificate(mock_http_certificate)

            call_args = mock_stream.call_args[0][0]
            assert "--server" in call_args
            server_index = call_args.index("--server")
            assert call_args[server_index + 1] == LETSENCRYPT_PRODUCTION_SERVER

    def test_generate_certificate_uses_letsencrypt_staging_with_env(
        self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate, monkeypatch
    ):
        """Test that staging Let's Encrypt server is used when FM_LETSENCRYPT_STAGING is set."""
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

        service = AcmeShCertificateService(
            ssl_service_dir=ssl_dir,
            webroot_dir=webroot_dir,
            output_handler=mock_output_handler,
        )

        with patch.object(service, "_stream_acmesh_command", return_value=0) as mock_stream:
            service.generate_certificate(mock_http_certificate)

            call_args = mock_stream.call_args[0][0]
            assert "--server" in call_args
            server_index = call_args.index("--server")
            assert call_args[server_index + 1] == LETSENCRYPT_STAGING_SERVER

    def test_renew_certificate_uses_letsencrypt_production_by_default(
        self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate
    ):
        """Test that production Let's Encrypt server is used for renewal by default."""
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

        dest_dir = ssl_dir / "acmesh" / "example.com"
        dest_dir.mkdir(parents=True)

        AcmeShCertificateService._acmesh_installed = False

        service = AcmeShCertificateService(
            ssl_service_dir=ssl_dir,
            webroot_dir=webroot_dir,
            output_handler=mock_output_handler,
        )

        with patch.object(service, "_stream_acmesh_command", return_value=0) as mock_stream:
            service.renew_certificate(mock_http_certificate)

            call_args = mock_stream.call_args[0][0]
            assert "--server" in call_args
            server_index = call_args.index("--server")
            assert call_args[server_index + 1] == LETSENCRYPT_PRODUCTION_SERVER

    def test_renew_certificate_uses_letsencrypt_staging_with_env(
        self, mock_logger, tmp_path, mock_output_handler, mock_http_certificate, monkeypatch
    ):
        """Test that staging Let's Encrypt server is used for renewal when FM_LETSENCRYPT_STAGING is set."""
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

        dest_dir = ssl_dir / "acmesh" / "example.com"
        dest_dir.mkdir(parents=True)

        AcmeShCertificateService._acmesh_installed = False

        service = AcmeShCertificateService(
            ssl_service_dir=ssl_dir,
            webroot_dir=webroot_dir,
            output_handler=mock_output_handler,
        )

        with patch.object(service, "_stream_acmesh_command", return_value=0) as mock_stream:
            service.renew_certificate(mock_http_certificate)

            call_args = mock_stream.call_args[0][0]
            assert "--server" in call_args
            server_index = call_args.index("--server")
            assert call_args[server_index + 1] == LETSENCRYPT_STAGING_SERVER
