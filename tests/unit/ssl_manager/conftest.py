"""
Shared fixtures for SSL Manager tests.

This module provides common fixtures used across multiple test files,
including mock certificates, storage configurations, and dependencies.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, MagicMock

import pytest
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.ssl_manager.storage_config import SSLStorageConfig


# ============================================================================
# Pytest Configuration
# ============================================================================

def pytest_addoption(parser):
    """Add custom pytest command line options."""
    parser.addoption(
        "--show-app-logs",
        action="store_true",
        default=False,
        help="Show application logs during tests (useful for debugging)"
    )


@pytest.fixture(autouse=True)
def configure_logging(request):
    """
    Suppress application logging during tests for clean output.
    
    By default, this suppresses all frappe_manager logs to avoid clutter
    from expected error conditions during testing. Use --show-app-logs flag
    to enable logging for debugging purposes.
    
    Usage:
        pytest tests/unit/ssl_manager/ -v               # Clean output (default)
        pytest tests/unit/ssl_manager/ -v --show-app-logs  # With logs for debugging
    """
    # Store original log level
    fm_logger = logging.getLogger('frappe_manager')
    original_level = fm_logger.level
    
    # Suppress logs unless --show-app-logs flag is used
    if not request.config.getoption("--show-app-logs"):
        fm_logger.setLevel(logging.CRITICAL)
    
    yield
    
    # Restore original log level
    fm_logger.setLevel(original_level)


# ============================================================================
# Certificate Fixtures
# ============================================================================

@pytest.fixture
def mock_certificate():
    """Returns a basic SSLCertificate instance with disable type."""
    return SSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.none,
        hsts="off"
    )


@pytest.fixture
def mock_letsencrypt_certificate_http01():
    """Returns LetsencryptSSLCertificate configured for HTTP-01 challenge."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
        email="admin@example.com",
        hsts="off"
    )


@pytest.fixture
def mock_letsencrypt_certificate_dns01():
    """Returns LetsencryptSSLCertificate configured for DNS-01 challenge with API token."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
        email="admin@example.com",
        api_token="test_cloudflare_token_123",
        hsts="off"
    )


@pytest.fixture
def mock_letsencrypt_certificate_dns01_with_key():
    """Returns LetsencryptSSLCertificate configured for DNS-01 with API key."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        preferred_challenge=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
        email="admin@example.com",
        api_key="test_cloudflare_global_key_456",
        hsts="off"
    )


# ============================================================================
# Storage Fixtures
# ============================================================================

@pytest.fixture
def valid_storage_paths(tmp_path):
    """
    Creates and returns a valid directory structure for SSL storage.
    
    Returns a dict with paths to all created directories.
    """
    # Create host directories
    ssl_dir = tmp_path / "ssl"
    ssl_dir.mkdir()
    
    certs_dir = tmp_path / "certs"
    certs_dir.mkdir()
    
    webroot_dir = tmp_path / "webroot"
    webroot_dir.mkdir()
    
    # Create subdirectories
    (ssl_dir / "letsencrypt").mkdir()
    (webroot_dir / ".well-known").mkdir()
    
    return {
        "ssl_dir": ssl_dir,
        "certs_dir": certs_dir,
        "webroot_dir": webroot_dir,
        "ssl_dir_container": Path("/etc/nginx/ssl"),
        "certs_dir_container": Path("/etc/nginx/certs"),
    }


@pytest.fixture
def mock_storage_config(tmp_path):
    """Returns SSLStorageConfig with temporary test directories."""
    paths = {
        "ssl_dir": tmp_path / "ssl",
        "certs_dir": tmp_path / "certs",
        "webroot_dir": tmp_path / "webroot",
    }
    
    # Create directories
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    
    return SSLStorageConfig(
        ssl_dir=paths["ssl_dir"],
        ssl_dir_container=Path("/etc/nginx/ssl"),
        certs_dir=paths["certs_dir"],
        certs_dir_container=Path("/etc/nginx/certs"),
        webroot_dir=paths["webroot_dir"],
        validate_on_init=False
    )


# ============================================================================
# Dependency Mocks
# ============================================================================

@pytest.fixture
def mock_compose_project(mocker):
    """Returns a mock ComposeProject instance."""
    mock_project = MagicMock()
    mock_project.running = True
    mock_project.docker = MagicMock()
    mock_project.docker.compose = MagicMock()
    mock_project.docker.compose.exec = MagicMock(return_value="OK")
    mock_project.docker.compose.restart = MagicMock(return_value="OK")
    mock_project.compose_file_manager = MagicMock()
    return mock_project


@pytest.fixture
def mock_ssl_service(mocker):
    """Returns a mock SSLCertificateService."""
    mock_service = MagicMock()
    mock_service.root_dir = Path("/tmp/ssl")
    
    # Setup default return values
    mock_service.generate_certificate.return_value = (
        Path("/tmp/ssl/privkey.pem"),
        Path("/tmp/ssl/fullchain.pem")
    )
    mock_service.renew_certificate.return_value = True
    mock_service.remove_certificate.return_value = True
    
    return mock_service


@pytest.fixture
def mock_link_manager(mocker):
    """Returns a mock CertificateLinkManager."""
    mock_manager = MagicMock()
    
    # Setup default return values
    mock_manager.get_certificate_paths.return_value = (
        Path("/tmp/ssl/privkey.pem"),
        Path("/tmp/ssl/fullchain.pem")
    )
    mock_manager.link_certificate.return_value = None
    mock_manager.unlink_certificate.return_value = None
    
    return mock_manager


@pytest.fixture
def mock_nginx_controller(mocker):
    """Returns a mock NginxController."""
    mock_controller = MagicMock()
    mock_controller.reload.return_value = None
    mock_controller.restart.return_value = None
    return mock_controller


# ============================================================================
# Manager Fixtures
# ============================================================================

@pytest.fixture
def ssl_certificate_manager(
    mock_certificate,
    mock_ssl_service,
    mock_link_manager,
    mock_nginx_controller
):
    """Returns a fully initialized SSLCertificateManager for testing."""
    from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager
    
    return SSLCertificateManager(
        certificate=mock_certificate,
        service=mock_ssl_service,
        link_manager=mock_link_manager,
        nginx_controller=mock_nginx_controller
    )


# ============================================================================
# Helper Fixtures
# ============================================================================

@pytest.fixture
def mock_richprint(mocker):
    """Mocks all richprint methods."""
    mock_rich = MagicMock()
    mocker.patch('frappe_manager.ssl_manager.nginx_controller.richprint', mock_rich)
    mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.richprint', mock_rich)
    mocker.patch('frappe_manager.ssl_manager.no_op_certificate_service.richprint', mock_rich)
    mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate.richprint', mock_rich)
    return mock_rich


@pytest.fixture
def sample_expiry_date():
    """Returns a sample certificate expiry date 30 days in the future."""
    return datetime.now() + timedelta(days=30)


@pytest.fixture
def mock_certbot_modules(mocker):
    """Mocks all certbot internal modules for testing LetsEncrypt service."""
    mocks = {
        'cli': mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.cli'),
        'storage': mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.storage'),
        'plugins_disco': mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.plugins_disco'),
        'display_obj': mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.display_obj'),
        'crypto_util': mocker.patch('frappe_manager.ssl_manager.letsencrypt_certificate_service.crypto_util'),
        'make_or_verify_needed_dirs': mocker.patch(
            'frappe_manager.ssl_manager.letsencrypt_certificate_service.make_or_verify_needed_dirs'
        ),
    }
    
    # Setup default behaviors
    mock_config = MagicMock()
    mock_config.func = MagicMock()
    mocks['cli'].prepare_and_parse_args.return_value = mock_config
    
    mock_plugins = MagicMock()
    mocks['plugins_disco'].PluginsRegistry.find_all.return_value = mock_plugins
    
    # Mock storage for get_certificate_paths
    mock_renewal_cert = MagicMock()
    mock_renewal_cert.lineagename = "example.com"
    mock_renewal_cert.key_path = "/tmp/ssl/privkey.pem"
    mock_renewal_cert.fullchain_path = "/tmp/ssl/fullchain.pem"
    
    mocks['storage'].renewal_conf_files.return_value = ["/tmp/renewal/example.com.conf"]
    mocks['storage'].RenewableCert.return_value = mock_renewal_cert
    
    return mocks


@pytest.fixture(autouse=True)
def reset_env_vars(monkeypatch):
    """
    Automatically resets environment variables after each test.
    
    This ensures FM_LETSENCRYPT_STAGING and other env vars don't leak between tests.
    """
    # Store original values
    original_env = {}
    env_vars = ['FM_LETSENCRYPT_STAGING']
    
    for var in env_vars:
        import os
        original_env[var] = os.environ.get(var)
    
    yield
    
    # Restore original values
    import os
    for var, value in original_env.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


# ============================================================================
# Parametrize Helpers
# ============================================================================

# Common test domains for parametrized tests
TEST_DOMAINS = [
    "example.com",
    "test.example.com",
    "subdomain.test.example.com",
    "*.example.com",  # wildcard
    "localhost",
    "test-site.local",
]

# Common email addresses for validation tests
VALID_EMAILS = [
    "admin@example.com",
    "user.name@example.com",
    "test+tag@subdomain.example.com",
    "123@test.com",
]

INVALID_EMAILS = [
    "notanemail",
    "@example.com",
    "user@",
    "user @example.com",
    "user@.com",
]
