"""
Unit tests for SSLStorageConfig dataclass.

Tests the SSL storage configuration dataclass including initialization,
validation, and path management.
"""

import pytest
from pathlib import Path

from frappe_manager.ssl_manager.storage_config import SSLStorageConfig


class TestSSLStorageConfigInitialization:
    """Tests for SSLStorageConfig initialization."""

    def test_create_config_with_all_fields(self, tmp_path):
        """Test creating config with all required fields."""
        ssl_dir = tmp_path / "ssl"
        ssl_dir.mkdir()
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir()

        config = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=certs_dir,
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=webroot_dir,
            validate_on_init=False,
        )

        assert config.ssl_dir == ssl_dir
        assert config.ssl_dir_container == Path("/etc/nginx/ssl")
        assert config.certs_dir == certs_dir
        assert config.certs_dir_container == Path("/etc/nginx/certs")
        assert config.webroot_dir == webroot_dir

    def test_validate_on_init_false_no_validation(self, tmp_path):
        """Test that validate_on_init=False skips validation."""
        # Create config with non-existent paths
        config = SSLStorageConfig(
            ssl_dir=tmp_path / "nonexistent_ssl",
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=tmp_path / "nonexistent_certs",
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "nonexistent_webroot",
            validate_on_init=False,
        )

        # Should not raise error even though paths don't exist
        assert config.ssl_dir == tmp_path / "nonexistent_ssl"

    def test_all_fields_are_path_objects(self, tmp_path):
        """Test that all path fields are Path objects."""
        ssl_dir = tmp_path / "ssl"
        ssl_dir.mkdir()
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir()

        config = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=certs_dir,
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=webroot_dir,
        )

        assert isinstance(config.ssl_dir, Path)
        assert isinstance(config.ssl_dir_container, Path)
        assert isinstance(config.certs_dir, Path)
        assert isinstance(config.certs_dir_container, Path)
        assert isinstance(config.webroot_dir, Path)

    def test_default_validate_on_init_is_false(self, tmp_path):
        """Test that validate_on_init defaults to False."""
        config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=tmp_path / "certs",
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot",
        )

        assert config.validate_on_init is False


class TestSSLStorageConfigValidationSuccess:
    """Tests for successful validation scenarios."""

    def test_validate_on_init_true_with_valid_paths(self, tmp_path):
        """Test that validate_on_init=True succeeds with valid paths."""
        # Create all required directories
        ssl_dir = tmp_path / "ssl"
        ssl_dir.mkdir()
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir()

        # Should not raise error
        config = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=certs_dir,
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=webroot_dir,
            validate_on_init=True,
        )

        assert config.ssl_dir.exists()
        assert config.certs_dir.exists()
        assert config.webroot_dir.exists()

    def test_explicit_validate_call_with_valid_paths(self, tmp_path):
        """Test that explicit validate() call succeeds with valid paths."""
        ssl_dir = tmp_path / "ssl"
        ssl_dir.mkdir()
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir()

        config = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=certs_dir,
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=webroot_dir,
            validate_on_init=False,
        )

        # Should not raise error
        config.validate()


class TestSSLStorageConfigValidationFailures:
    """Tests for validation failure scenarios."""

    def test_validate_on_init_missing_ssl_dir_raises_error(self, tmp_path):
        """Test that validate_on_init=True raises error if ssl_dir missing."""
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir()

        with pytest.raises(ValueError) as exc_info:
            SSLStorageConfig(
                ssl_dir=tmp_path / "nonexistent_ssl",
                ssl_dir_container=Path("/etc/nginx/ssl"),
                certs_dir=certs_dir,
                certs_dir_container=Path("/etc/nginx/certs"),
                webroot_dir=webroot_dir,
                validate_on_init=True,
            )

        assert "SSL root directory does not exist" in str(exc_info.value)

    def test_validate_on_init_missing_certs_dir_raises_error(self, tmp_path):
        """Test that validate_on_init=True raises error if certs_dir missing."""
        ssl_dir = tmp_path / "ssl"
        ssl_dir.mkdir()
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir()

        with pytest.raises(ValueError) as exc_info:
            SSLStorageConfig(
                ssl_dir=ssl_dir,
                ssl_dir_container=Path("/etc/nginx/ssl"),
                certs_dir=tmp_path / "nonexistent_certs",
                certs_dir_container=Path("/etc/nginx/certs"),
                webroot_dir=webroot_dir,
                validate_on_init=True,
            )

        assert "Certificates directory does not exist" in str(exc_info.value)

    def test_validate_on_init_missing_webroot_dir_raises_error(self, tmp_path):
        """Test that validate_on_init=True raises error if webroot_dir missing."""
        ssl_dir = tmp_path / "ssl"
        ssl_dir.mkdir()
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()

        with pytest.raises(ValueError) as exc_info:
            SSLStorageConfig(
                ssl_dir=ssl_dir,
                ssl_dir_container=Path("/etc/nginx/ssl"),
                certs_dir=certs_dir,
                certs_dir_container=Path("/etc/nginx/certs"),
                webroot_dir=tmp_path / "nonexistent_webroot",
                validate_on_init=True,
            )

        assert "Webroot directory does not exist" in str(exc_info.value)

    def test_explicit_validate_with_missing_paths_raises_error(self, tmp_path):
        """Test that explicit validate() call raises error with missing paths."""
        config = SSLStorageConfig(
            ssl_dir=tmp_path / "nonexistent_ssl",
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=tmp_path / "nonexistent_certs",
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "nonexistent_webroot",
            validate_on_init=False,
        )

        with pytest.raises(ValueError):
            config.validate()


class TestSSLStorageConfigUsageScenarios:
    """Tests for real-world usage scenarios."""

    def test_config_with_nested_directory_structure(self, tmp_path):
        """Test config with nested directory paths."""
        ssl_dir = tmp_path / "nginx" / "ssl"
        ssl_dir.mkdir(parents=True)
        certs_dir = tmp_path / "nginx" / "certs"
        certs_dir.mkdir(parents=True)
        webroot_dir = tmp_path / "nginx" / "webroot"
        webroot_dir.mkdir(parents=True)

        config = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=certs_dir,
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=webroot_dir,
            validate_on_init=True,
        )

        assert config.ssl_dir.exists()
        assert "nginx" in str(config.ssl_dir)

    def test_config_container_paths_are_independent(self, tmp_path):
        """Test that container paths can differ from host paths."""
        ssl_dir = tmp_path / "host_ssl"
        ssl_dir.mkdir()
        certs_dir = tmp_path / "host_certs"
        certs_dir.mkdir()
        webroot_dir = tmp_path / "host_webroot"
        webroot_dir.mkdir()

        config = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/container/ssl"),
            certs_dir=certs_dir,
            certs_dir_container=Path("/container/certs"),
            webroot_dir=webroot_dir,
            validate_on_init=True,
        )

        # Container paths should be different from host paths
        assert config.ssl_dir != config.ssl_dir_container
        assert config.certs_dir != config.certs_dir_container

    def test_config_can_be_created_and_validated_separately(self, tmp_path):
        """Test that config can be created first, then validated later."""
        # Create config without validation
        config = SSLStorageConfig(
            ssl_dir=tmp_path / "ssl",
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=tmp_path / "certs",
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=tmp_path / "webroot",
            validate_on_init=False,
        )

        # Directories don't exist yet - validation would fail
        with pytest.raises(ValueError):
            config.validate()

        # Create directories
        config.ssl_dir.mkdir()
        config.certs_dir.mkdir()
        config.webroot_dir.mkdir()

        # Now validation should succeed
        config.validate()

    def test_multiple_configs_with_same_tmp_path(self, tmp_path):
        """Test creating multiple configs with the same temp directory."""
        ssl_dir = tmp_path / "ssl"
        ssl_dir.mkdir()
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        webroot_dir = tmp_path / "webroot"
        webroot_dir.mkdir()

        config1 = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/etc/nginx/ssl"),
            certs_dir=certs_dir,
            certs_dir_container=Path("/etc/nginx/certs"),
            webroot_dir=webroot_dir,
        )

        config2 = SSLStorageConfig(
            ssl_dir=ssl_dir,
            ssl_dir_container=Path("/container/ssl"),  # Different container path
            certs_dir=certs_dir,
            certs_dir_container=Path("/container/certs"),
            webroot_dir=webroot_dir,
        )

        # Both configs share same host paths
        assert config1.ssl_dir == config2.ssl_dir
        # But have different container paths
        assert config1.ssl_dir_container != config2.ssl_dir_container
