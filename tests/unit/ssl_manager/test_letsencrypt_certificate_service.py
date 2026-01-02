"""
Tests for frappe_manager.ssl_manager.letsencrypt_certificate_service module.

This module tests the LetsEncryptCertificateService class which integrates
with certbot to manage Let's Encrypt SSL certificates.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, call
from frappe_manager.ssl_manager.letsencrypt_certificate_service import LetsEncryptCertificateService
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateChallengeFailed,
    SSLCertificateGenerateFailed,
    SSLCertificateNotFoundError
)
from certbot.errors import AuthorizationError


class TestLetsEncryptCertificateServiceInitialization:
    """Tests for LetsEncryptCertificateService initialization."""
    
    def test_init_creates_correct_directory_structure(self, tmp_path):
        """Test that initialization sets up correct directory paths."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        assert service.webroot_dir == webroot_dir
        assert service.root_dir == ssl_service_dir / "letsencrypt"
        assert service.config_dir == ssl_service_dir / "letsencrypt" / "config"
        assert service.work_dir == ssl_service_dir / "letsencrypt" / "work"
        assert service.logs_dir == ssl_service_dir / "letsencrypt" / "logs"
        assert service.dns_config_dir == ssl_service_dir / "letsencrypt" / "dns_configs"
    
    def test_init_creates_base_command(self, tmp_path):
        """Test that initialization creates the correct base certbot command."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        assert "--work-dir" in service.base_command
        assert "--config-dir" in service.base_command
        assert "--logs-dir" in service.base_command
        assert str(service.work_dir) in service.base_command
        assert str(service.config_dir) in service.base_command
        assert str(service.logs_dir) in service.base_command
    
    def test_init_creates_console_output_stream(self, tmp_path):
        """Test that initialization creates a StringIO for console output."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        assert hasattr(service, 'console_output')
        assert hasattr(service.console_output, 'write')
        assert hasattr(service.console_output, 'getvalue')


class TestLetsEncryptCertificateServiceRenewCertificate:
    """Tests for LetsEncryptCertificateService.renew_certificate method."""
    
    def test_renew_certificate_calls_certbot_with_correct_command(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that renew_certificate builds and executes the correct certbot command."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_make_dirs = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.make_or_verify_needed_dirs')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        
        service.renew_certificate(mock_letsencrypt_certificate_http01)
        
        # Verify _get_le_config was called with renew command
        call_args = mock_get_config.call_args[0][0]
        assert 'renew' in call_args
        assert '--cert-name' in call_args
        assert mock_letsencrypt_certificate_http01.domain in call_args
        assert '--non-interactive' in call_args
        assert '--force-renewal' in call_args
        
        # Verify certbot function was called
        mock_config.func.assert_called_once()
    
    def test_renew_certificate_uses_staging_when_env_var_set(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that renew_certificate uses staging server when FM_LETSENCRYPT_STAGING is set."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock environment variable
        mocker.patch('os.getenv', return_value='true')
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_make_dirs = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.make_or_verify_needed_dirs')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        
        service.renew_certificate(mock_letsencrypt_certificate_http01)
        
        # Verify --test-cert flag is in the command
        call_args = mock_get_config.call_args[0][0]
        assert '--test-cert' in call_args
        
        # Verify warning message was printed
        mock_richprint.print.assert_called()


class TestLetsEncryptCertificateServiceGetCertificatePaths:
    """Tests for LetsEncryptCertificateService.get_certificate_paths method."""
    
    def test_get_certificate_paths_returns_correct_paths(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that get_certificate_paths returns the correct certificate file paths."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_storage = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.storage')
        mock_crypto_util = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.crypto_util')
        
        # Create mock renewal certificate
        mock_renewal_cert = MagicMock()
        mock_renewal_cert.lineagename = mock_letsencrypt_certificate_http01.domain
        mock_renewal_cert.key_path = "/etc/letsencrypt/live/example.com/privkey.pem"
        mock_renewal_cert.fullchain_path = "/etc/letsencrypt/live/example.com/fullchain.pem"
        
        mock_storage.renewal_conf_files.return_value = [Path("/etc/letsencrypt/renewal/example.com.conf")]
        mock_storage.RenewableCert.return_value = mock_renewal_cert
        
        privkey_path, fullchain_path = service.get_certificate_paths(mock_letsencrypt_certificate_http01)
        
        assert privkey_path == Path("/etc/letsencrypt/live/example.com/privkey.pem")
        assert fullchain_path == Path("/etc/letsencrypt/live/example.com/fullchain.pem")
    
    def test_get_certificate_paths_raises_if_certificate_not_found(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that get_certificate_paths raises SSLCertificateNotFoundError if cert doesn't exist."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals to return no certificates
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_storage = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.storage')
        
        mock_storage.renewal_conf_files.return_value = []
        
        with pytest.raises(SSLCertificateNotFoundError) as exc_info:
            service.get_certificate_paths(mock_letsencrypt_certificate_http01)
        
        assert mock_letsencrypt_certificate_http01.domain in str(exc_info.value)


class TestLetsEncryptCertificateServiceRemoveCertificate:
    """Tests for LetsEncryptCertificateService.remove_certificate method."""
    
    def test_remove_certificate_calls_certbot_delete(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that remove_certificate calls certbot delete command."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        
        service.remove_certificate(mock_letsencrypt_certificate_http01)
        
        # Verify delete command was used
        call_args = mock_get_config.call_args[0][0]
        assert 'delete' in call_args
        assert '--cert-name' in call_args
        assert mock_letsencrypt_certificate_http01.domain in call_args
        
        # Verify certbot function was called
        mock_config.func.assert_called_once()
        
        # Verify status messages
        mock_richprint.change_head.assert_called_with("Removing Letsencrypt certificate")
        mock_richprint.print.assert_called_with("Removed Letsencrypt certificate")


class TestLetsEncryptCertificateServiceGenerateCertificateHTTP01:
    """Tests for LetsEncryptCertificateService.generate_certificate with HTTP-01 challenge."""
    
    def test_generate_certificate_http01_builds_correct_command(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that generate_certificate builds correct command for HTTP-01 challenge."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        mock_get_paths = mocker.patch.object(service, 'get_certificate_paths')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        mock_get_paths.return_value = (Path("/privkey.pem"), Path("/fullchain.pem"))
        
        service.generate_certificate(mock_letsencrypt_certificate_http01)
        
        # Verify command includes HTTP-01 specific flags
        call_args = mock_get_config.call_args[0][0]
        assert 'certonly' in call_args
        assert '--webroot' in call_args
        assert '-w' in call_args
        assert str(webroot_dir) in call_args
        assert '--agree-tos' in call_args
        assert '-m' in call_args
        assert mock_letsencrypt_certificate_http01.email in call_args
        assert '-d' in call_args
        assert mock_letsencrypt_certificate_http01.domain in call_args
    
    def test_generate_certificate_http01_with_alias_domains(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that generate_certificate includes alias domains in the certificate."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        mock_get_paths = mocker.patch.object(service, 'get_certificate_paths')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        mock_get_paths.return_value = (Path("/privkey.pem"), Path("/fullchain.pem"))
        
        alias_domains = ["www.example.com", "api.example.com"]
        service.generate_certificate(mock_letsencrypt_certificate_http01, alias_domains=alias_domains)
        
        # Verify all domains are in the command
        call_args = mock_get_config.call_args[0][0]
        command_str = ' '.join(call_args)
        
        assert mock_letsencrypt_certificate_http01.domain in command_str
        assert "www.example.com" in command_str
        assert "api.example.com" in command_str


class TestLetsEncryptCertificateServiceGenerateCertificateDNS01:
    """Tests for LetsEncryptCertificateService.generate_certificate with DNS-01 challenge."""
    
    def test_generate_certificate_dns01_creates_credentials_file(self, mocker, tmp_path, mock_letsencrypt_certificate_dns01):
        """Test that generate_certificate creates Cloudflare credentials file for DNS-01."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        mock_get_paths = mocker.patch.object(service, 'get_certificate_paths')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        mock_get_paths.return_value = (Path("/privkey.pem"), Path("/fullchain.pem"))
        
        service.generate_certificate(mock_letsencrypt_certificate_dns01)
        
        # Verify DNS config file was created
        dns_config_path = service.dns_config_dir / f'{mock_letsencrypt_certificate_dns01.domain}.txt'
        assert dns_config_path.exists()
        
        # Verify credentials are in the file
        content = dns_config_path.read_text()
        assert 'dns_cloudflare_api_token' in content or 'dns_cloudflare_email' in content
        
        # Verify command includes DNS-01 specific flags
        call_args = mock_get_config.call_args[0][0]
        assert '--dns-cloudflare' in call_args
        assert '--dns-cloudflare-credentials' in call_args
    
    def test_generate_certificate_dns01_removes_credentials_on_failure(self, mocker, tmp_path, mock_letsencrypt_certificate_dns01):
        """Test that credentials file is removed if certificate generation fails."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot internals to raise error
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        
        mock_config = MagicMock()
        mock_config.func.side_effect = AuthorizationError("DNS challenge failed")
        mock_get_config.return_value = mock_config
        
        dns_config_path = service.dns_config_dir / f'{mock_letsencrypt_certificate_dns01.domain}.txt'
        
        with pytest.raises(SSLCertificateChallengeFailed):
            service.generate_certificate(mock_letsencrypt_certificate_dns01)
        
        # Verify credentials file was removed after failure
        assert not dns_config_path.exists()


class TestLetsEncryptCertificateServiceGenerateCertificateErrors:
    """Tests for error handling in generate_certificate."""
    
    def test_generate_certificate_raises_challenge_failed_on_authorization_error(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that generate_certificate raises SSLCertificateChallengeFailed on AuthorizationError."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot to raise AuthorizationError
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        
        mock_config = MagicMock()
        mock_config.func.side_effect = AuthorizationError("Challenge failed")
        mock_get_config.return_value = mock_config
        
        with pytest.raises(SSLCertificateChallengeFailed):
            service.generate_certificate(mock_letsencrypt_certificate_http01)
    
    def test_generate_certificate_raises_generate_failed_on_generic_error(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that generate_certificate raises SSLCertificateGenerateFailed on generic errors."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot to raise generic error
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        
        mock_config = MagicMock()
        mock_config.func.side_effect = Exception("Unexpected error")
        mock_get_config.return_value = mock_config
        
        with pytest.raises(SSLCertificateGenerateFailed):
            service.generate_certificate(mock_letsencrypt_certificate_http01)


class TestLetsEncryptCertificateServiceGetLEConfig:
    """Tests for LetsEncryptCertificateService._get_le_config method."""
    
    def test_get_le_config_with_quiet_mode(self, mocker, tmp_path):
        """Test that _get_le_config sets quiet mode correctly."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot cli
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_cli = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.cli')
        mock_display = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.display_obj')
        
        mock_config = MagicMock()
        mock_cli.prepare_and_parse_args.return_value = mock_config
        
        service._get_le_config(['certonly'], quiet=True)
        
        # Verify quiet settings were applied
        assert mock_config.quiet is True
        assert mock_config.noninteractive_mode is True
        mock_display.set_display.assert_called_once()
    
    def test_get_le_config_without_quiet_mode(self, mocker, tmp_path):
        """Test that _get_le_config doesn't set quiet mode when not requested."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock certbot cli
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_cli = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.cli')
        mock_display = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.display_obj')
        
        mock_config = MagicMock()
        mock_cli.prepare_and_parse_args.return_value = mock_config
        
        service._get_le_config(['certonly'], quiet=False)
        
        # Verify quiet settings were not applied
        mock_display.set_display.assert_not_called()


class TestLetsEncryptCertificateServiceStagingMode:
    """Tests for staging/production mode handling."""
    
    def test_generate_certificate_uses_staging_when_env_var_is_1(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that staging mode is enabled when FM_LETSENCRYPT_STAGING=1."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock environment
        mocker.patch('os.getenv', return_value='1')
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        mock_get_paths = mocker.patch.object(service, 'get_certificate_paths')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        mock_get_paths.return_value = (Path("/privkey.pem"), Path("/fullchain.pem"))
        
        service.generate_certificate(mock_letsencrypt_certificate_http01)
        
        # Verify --test-cert is in command
        call_args = mock_get_config.call_args[0][0]
        assert '--test-cert' in call_args
    
    def test_generate_certificate_uses_production_when_env_var_not_set(self, mocker, tmp_path, mock_letsencrypt_certificate_http01):
        """Test that production mode is used when FM_LETSENCRYPT_STAGING is not set."""
        ssl_service_dir = tmp_path / "ssl"
        webroot_dir = tmp_path / "webroot"
        
        service = LetsEncryptCertificateService(ssl_service_dir, webroot_dir)
        
        # Mock environment - return empty string (not set)
        mocker.patch('os.getenv', return_value='')
        
        # Mock certbot internals
        mock_get_config = mocker.patch.object(service, '_get_le_config')
        mock_plugins = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco.PluginsRegistry.find_all')
        mock_richprint = mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint')
        mock_get_paths = mocker.patch.object(service, 'get_certificate_paths')
        
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config
        mock_get_paths.return_value = (Path("/privkey.pem"), Path("/fullchain.pem"))
        
        service.generate_certificate(mock_letsencrypt_certificate_http01)
        
        # Verify --test-cert is NOT in command
        call_args = mock_get_config.call_args[0][0]
        assert '--test-cert' not in call_args
