"""
Shared pytest fixtures for integration tests.

This module provides fixtures that are automatically available to all integration tests.
"""

import pytest

from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.rich_output import RichOutputHandler


@pytest.fixture(autouse=True)
def init_global_output_handler():
    """
    Initialize global output handler for all tests.

    This fixture runs automatically before every test to ensure the global
    output handler is initialized, which is required by app_callback() and
    other functions that use get_global_output_handler().

    After each test, the handler is reset to None to ensure test isolation.
    """
    # Initialize with a basic RichOutputHandler (no logging)
    handler = RichOutputHandler()
    set_global_output_handler(handler)

    yield

    # Reset after test to ensure isolation
    set_global_output_handler(None)


@pytest.fixture(autouse=True)
def mock_app_infrastructure():
    """Mock Docker daemon and infrastructure checks so CLI commands succeed in tests."""
    from unittest.mock import MagicMock, patch

    from frappe_manager.__about__ import __version__
    from frappe_manager.migration_manager.version import Version

    mock_config = MagicMock()
    mock_config.logs.file_level = "DEBUG"
    # Return current version so infra_needs_migration = False (no migration prompt)
    mock_config.get_system_migration_version.return_value = Version(__version__)

    with (
        patch("frappe_manager.commands.DockerClient") as mock_docker,
        patch("frappe_manager.commands.CLI_FM_CONFIG_PATH") as mock_config_path,
        patch("frappe_manager.commands.CLI_DIR") as mock_cli_dir,
        patch("frappe_manager.commands.FMConfigManager") as mock_fm_config_cls,
    ):
        mock_docker.return_value.server_running.return_value = True
        mock_config_path.exists.return_value = True
        mock_cli_dir.exists.return_value = True
        mock_cli_dir.is_dir.return_value = True
        mock_fm_config_cls.import_from_toml.return_value = mock_config
        yield
