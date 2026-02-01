"""
Unit tests for AppConfig model.

Tests the parsing and validation of app configuration strings.
"""

import pytest
from typing import Optional, Dict
from frappe_manager.site_manager.bench_config import AppConfig


class TestAppConfigParsing:
    """Test AppConfig.from_string() parsing."""

    def test_parse_simple_app_name(self):
        """Test parsing simple app name (infers frappe repo)."""
        config = AppConfig.from_string("erpnext")

        assert config.name == "erpnext"
        assert config.repo == "frappe/erpnext"
        assert config.ref is None
        assert config.subdir_path is None
        assert config.shallow_clone is True
        assert config.symlink is False

    def test_parse_app_with_branch(self):
        """Test parsing app with branch."""
        config = AppConfig.from_string("erpnext:version-15")

        assert config.name == "erpnext"
        assert config.repo == "frappe/erpnext"
        assert config.ref == "version-15"
        assert config.subdir_path is None

    def test_parse_full_repo_path(self):
        """Test parsing full repo path."""
        config = AppConfig.from_string("frappe/erpnext:version-15")

        assert config.name == "erpnext"
        assert config.repo == "frappe/erpnext"
        assert config.ref == "version-15"

    def test_parse_custom_repo(self):
        """Test parsing custom organization repo."""
        config = AppConfig.from_string("mycompany/custom-app:main")

        assert config.name == "custom-app"
        assert config.repo == "mycompany/custom-app"
        assert config.ref == "main"

    def test_parse_subdirectory_app(self):
        """Test parsing subdirectory app (monorepo)."""
        config = AppConfig.from_string("frappe/frappe:version-15#apps/frappe")

        assert config.name == "frappe"
        assert config.repo == "frappe/frappe"
        assert config.ref == "version-15"
        assert config.subdir_path == "apps/frappe"

    def test_parse_subdirectory_without_branch(self):
        """Test parsing subdirectory app without branch."""
        config = AppConfig.from_string("frappe/frappe#apps/frappe")

        assert config.name == "frappe"
        assert config.repo == "frappe/frappe"
        assert config.ref is None
        assert config.subdir_path == "apps/frappe"

    def test_parse_with_github_token(self):
        """Test parsing with GitHub token (repo_url should be None to allow fallback)."""
        token = "ghp_test123"
        config = AppConfig.from_string("erpnext:version-15", github_token=token)

        assert config.name == "erpnext"
        assert config.repo == "frappe/erpnext"
        assert config.ref == "version-15"
        assert config.repo_url is None

    def test_parse_without_github_token(self):
        """Test parsing without GitHub token (repo_url should be None to allow fallback)."""
        config = AppConfig.from_string("erpnext:version-15")

        assert config.repo_url is None

    def test_parse_rtcamp_org(self):
        """Test parsing app from rtcamp organization."""
        config = AppConfig.from_string("rtcamp/frappe-manager:main")

        assert config.name == "frappe-manager"
        assert config.repo == "rtcamp/frappe-manager"
        assert config.ref == "main"

    def test_parse_custom_org_without_branch(self):
        """Test parsing custom org without specifying branch."""
        config = AppConfig.from_string("myorg/myapp")

        assert config.name == "myapp"
        assert config.repo == "myorg/myapp"
        assert config.ref is None

    def test_parse_explicit_frappe_org(self):
        """Test that explicit frappe/ org works same as implicit."""
        config1 = AppConfig.from_string("helpdesk:v1.9.1")
        config2 = AppConfig.from_string("frappe/helpdesk:v1.9.1")

        assert config1.name == config2.name == "helpdesk"
        assert config1.repo == config2.repo == "frappe/helpdesk"
        assert config1.ref == config2.ref == "v1.9.1"


class TestAppConfigCommitDetection:
    """Test commit SHA detection."""

    def test_is_commit_with_sha(self):
        """Test is_commit property with 40-character hex SHA."""
        config = AppConfig.from_string("erpnext:a1b2c3d4e5f6789012345678901234567890abcd")

        assert config.is_commit is True

    def test_is_commit_with_branch(self):
        """Test is_commit property with branch name."""
        config = AppConfig.from_string("erpnext:version-15")

        assert config.is_commit is False

    def test_is_commit_with_tag(self):
        """Test is_commit property with tag."""
        config = AppConfig.from_string("erpnext:v14.0.0")

        assert config.is_commit is False

    def test_is_commit_without_ref(self):
        """Test is_commit property without ref."""
        config = AppConfig.from_string("erpnext")

        assert config.is_commit is False


class TestAppConfigFromDict:
    """Test AppConfig.from_dict() conversion."""

    def test_from_dict_simple(self):
        """Test converting simple dict format."""
        app_dict: Dict[str, Optional[str]] = {"app": "erpnext", "branch": "version-15"}
        config = AppConfig.from_dict(app_dict)

        assert config.name == "erpnext"
        assert config.repo == "frappe/erpnext"
        assert config.ref == "version-15"

    def test_from_dict_without_branch(self):
        """Test converting dict without branch."""
        app_dict: Dict[str, Optional[str]] = {"app": "erpnext", "branch": None}
        config = AppConfig.from_dict(app_dict)

        assert config.name == "erpnext"
        assert config.ref is None

    def test_from_dict_with_token(self):
        """Test converting dict with GitHub token (repo_url should be None to allow fallback)."""
        app_dict: Dict[str, Optional[str]] = {"app": "mycompany/private-app", "branch": "main"}
        token = "ghp_test123"
        config = AppConfig.from_dict(app_dict, github_token=token)

        assert config.name == "private-app"
        assert config.repo == "mycompany/private-app"
        assert config.repo_url is None

    def test_from_dict_invalid_empty_app(self):
        """Test that empty app name raises ValueError."""
        app_dict: Dict[str, Optional[str]] = {"app": "", "branch": "main"}

        with pytest.raises(ValueError, match="app_dict must contain 'app' key"):
            AppConfig.from_dict(app_dict)

    def test_from_dict_invalid_missing_app(self):
        """Test that missing app key raises ValueError."""
        app_dict: Dict[str, Optional[str]] = {"branch": "main"}

        with pytest.raises(ValueError, match="app_dict must contain 'app' key"):
            AppConfig.from_dict(app_dict)


class TestAppConfigFieldDefaults:
    """Test default field values."""

    def test_default_shallow_clone(self):
        """Test shallow_clone defaults to True."""
        config = AppConfig.from_string("erpnext")
        assert config.shallow_clone is True

    def test_default_symlink(self):
        """Test symlink defaults to False."""
        config = AppConfig.from_string("erpnext")
        assert config.symlink is False

    def test_default_subdir_path(self):
        """Test subdir_path defaults to None."""
        config = AppConfig.from_string("erpnext")
        assert config.subdir_path is None


class TestAppConfigEdgeCases:
    """Test edge cases and special scenarios."""

    def test_parse_app_with_dashes(self):
        """Test parsing app with dashes in name."""
        config = AppConfig.from_string("custom-app-name:main")
        assert config.name == "custom-app-name"

    def test_parse_app_with_underscores(self):
        """Test parsing app with underscores."""
        config = AppConfig.from_string("custom_app_name:main")
        assert config.name == "custom_app_name"

    def test_parse_nested_subdirectory(self):
        """Test parsing nested subdirectory path."""
        config = AppConfig.from_string("company/monorepo:main#packages/apps/custom")

        assert config.name == "custom"
        assert config.subdir_path == "packages/apps/custom"

    def test_parse_subdirectory_extracts_correct_name(self):
        """Test that subdirectory path correctly extracts app name."""
        config = AppConfig.from_string("frappe/payments:main#apps/payments_integration")

        assert config.name == "payments_integration"
        assert config.repo == "frappe/payments"
