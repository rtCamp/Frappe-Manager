"""Pytest fixtures for migration_manager tests."""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock
from frappe_manager.migration_manager.version import Version


@pytest.fixture
def mock_bench_path(tmp_path):
    """Create a temporary bench directory structure."""
    bench_path = tmp_path / "test-bench"
    bench_path.mkdir()

    # Create docker-compose.yml
    compose_file = bench_path / "docker-compose.yml"
    compose_file.write_text("""
version: "3.9"
services:
  frappe:
    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0
    container_name: test-bench-frappe
  nginx:
    image: ghcr.io/rtcamp/frappe-manager-nginx:v0.18.0
    container_name: test-bench-nginx
""")

    # Create workspace directory structure
    workspace = bench_path / "workspace" / "frappe-bench" / "sites"
    workspace.mkdir(parents=True)

    # Create common_site_config.json
    config_file = workspace / "common_site_config.json"
    config_file.write_text('{"db_host": "mariadb", "redis_cache": "redis://redis-cache:6379"}')

    return bench_path


@pytest.fixture
def mock_services_path(tmp_path):
    """Create a temporary services directory."""
    services_path = tmp_path / "services"
    services_path.mkdir()
    return services_path


@pytest.fixture
def mock_fm_config():
    """Mock FMConfigManager."""
    config = Mock()
    config.version = Version("0.18.0")
    return config


@pytest.fixture
def mock_compose_project():
    """Mock ComposeProject."""
    project = MagicMock()
    project.compose_file_manager = MagicMock()
    project.compose_file_manager.compose_path = Path("/tmp/docker-compose.yml")
    project.compose_file_manager.yml = {
        "version": "3.9",
        "services": {"frappe": {"image": "frappe:latest"}, "nginx": {"image": "nginx:latest"}},
    }
    project.stop_service = MagicMock()
    return project
