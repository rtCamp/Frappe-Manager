"""
Shared fixtures for SSL Manager tests.

This module provides common fixtures used across multiple test files,
including mock certificates, storage configurations, and dependencies.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
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
        help="Show application logs during tests (useful for debugging)",
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
    fm_logger = logging.getLogger("frappe_manager")
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
    return SSLCertificate(domain="example.com", ssl_type=SUPPORTED_SSL_TYPES.none, hsts="off")


@pytest.fixture
def mock_letsencrypt_certificate_http01():
    """Returns LetsencryptSSLCertificate configured for HTTP-01 challenge."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
        hsts="off",
        api_token=None,
        api_key=None,
    )


@pytest.fixture
def mock_letsencrypt_certificate_dns01():
    """Returns LetsencryptSSLCertificate configured for DNS-01 challenge with API token."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
        api_token="test_cloudflare_token_123",
        api_key=None,
        hsts="off",
    )


@pytest.fixture
def mock_letsencrypt_certificate_dns01_with_key():
    """Returns LetsencryptSSLCertificate configured for DNS-01 with API key."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
        api_token=None,
        api_key="test_cloudflare_global_key_456",
        hsts="off",
    )


@pytest.fixture
def mock_http_certificate():
    """Returns a certificate configured for HTTP-01 challenge (alias for http01)."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.http01,
        hsts="off",
        api_token=None,
        api_key=None,
    )


@pytest.fixture
def mock_dns_certificate():
    """Returns a certificate configured for DNS-01 challenge (alias for dns01)."""
    return LetsencryptSSLCertificate(
        domain="example.com",
        ssl_type=SUPPORTED_SSL_TYPES.le,
        challenge_type=LETSENCRYPT_PREFERRED_CHALLENGE.dns01,
        api_token="test_cloudflare_token_123",
        api_key=None,
        hsts="off",
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
        "vhostd_dir": tmp_path / "vhostd",
    }

    # Create directories
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    return SSLStorageConfig(
        ssl_dir=paths["ssl_dir"],
        ssl_dir_container=Path("/etc/nginx/ssl"),
        certs_dir=paths["certs_dir"],
        certs_dir_container=Path("/etc/nginx/certs"),
        vhostd_dir=paths["vhostd_dir"],
        webroot_dir=paths["webroot_dir"],
        validate_on_init=False,
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
def mock_compose_file_manager(mocker):
    """Returns a mock ComposeFile instance."""
    mock_cf = MagicMock()
    mock_cf.get_services_list.return_value = ["nginx-proxy"]
    mock_cf.get_container_names.return_value = {"nginx-proxy": "nginx-proxy-container"}
    mock_cf.get_service_volumes.return_value = []
    return mock_cf


@pytest.fixture
def mock_docker_client(mocker):
    """Returns a mock DockerClient instance."""
    mock_client = MagicMock()
    mock_client.compose = MagicMock()
    mock_client.compose.exec = MagicMock(return_value="OK")
    mock_client.compose.restart = MagicMock(return_value="OK")
    mock_client.compose.get_all_services_status.return_value = [
        {"Name": "nginx-proxy-container", "Service": "nginx-proxy", "State": "running"},
    ]
    return mock_client


@pytest.fixture
def mock_ssl_service(mocker):
    """Returns a mock SSLCertificateService."""
    mock_service = MagicMock()
    mock_service.root_dir = Path("/tmp/ssl")

    # Setup default return values
    mock_service.generate_certificate.return_value = (Path("/tmp/ssl/privkey.pem"), Path("/tmp/ssl/fullchain.pem"))
    mock_service.renew_certificate.return_value = True
    mock_service.remove_certificate.return_value = True

    return mock_service


@pytest.fixture
def mock_link_manager(mocker):
    """Returns a mock CertificateLinkManager."""
    mock_manager = MagicMock()

    # Setup default return values
    mock_manager.get_certificate_paths.return_value = (Path("/tmp/ssl/privkey.pem"), Path("/tmp/ssl/fullchain.pem"))
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


@pytest.fixture
def mock_output_handler(mocker):
    """Returns a mock OutputHandler."""
    mock_handler = MagicMock()
    mock_handler.display_info.return_value = None
    mock_handler.display_warning.return_value = None
    mock_handler.display_error.return_value = None
    mock_handler.start_progress.return_value = MagicMock()
    return mock_handler


@pytest.fixture
def mock_logger(mocker):
    """Returns a mock ContextualLogger."""
    mock_log = mocker.MagicMock()
    mock_log.child.return_value = mock_log
    return mock_log


# ============================================================================
# Manager Fixtures
# ============================================================================


@pytest.fixture
def ssl_certificate_manager(
    mocker,
    mock_certificate,
    mock_storage_config,
    mock_link_manager,
    mock_nginx_controller,
    mock_output_handler,
    mock_ssl_service,
):
    """Returns a fully initialized SSLCertificateManager for testing (multi-cert API)."""
    from frappe_manager.ssl_manager.ssl_certificate_manager import SSLCertificateManager

    mock_logger = mocker.MagicMock()
    mock_logger.child.return_value = mock_logger

    def certificate_service_factory(cert, storage_cfg, output_handler):
        return mock_ssl_service

    return SSLCertificateManager(
        logger=mock_logger,
        certificates=[mock_certificate],
        service_factory=certificate_service_factory,
        link_manager=mock_link_manager,
        nginx_controller=mock_nginx_controller,
        storage_config=mock_storage_config,
        output_handler=mock_output_handler,
        config_save_callback=None,
    )


# ============================================================================
# Helper Fixtures
# ============================================================================


@pytest.fixture
def sample_expiry_date():
    """Returns a sample certificate expiry date 30 days in the future."""
    return datetime.now() + timedelta(days=30)


@pytest.fixture(autouse=True)
def reset_env_vars(monkeypatch):
    """
    Automatically resets environment variables after each test.

    This ensures FM_LETSENCRYPT_STAGING and other env vars don't leak between tests.
    """
    # Store original values
    original_env = {}
    env_vars = ["FM_LETSENCRYPT_STAGING"]

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
