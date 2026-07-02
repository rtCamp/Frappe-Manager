"""
Unit tests for AppConfig model.

Tests the parsing and validation of app configuration strings.
"""


import pytest

from frappe_manager.site_manager.bench_config import AppBatchValidationResult, AppConfig, AppValidationResult


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
        """Test parsing ensures repo_url is None to allow auth fallback."""
        config = AppConfig.from_string("erpnext:version-15")

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

    def test_parse_https_url_with_ref(self):
        config = AppConfig.from_string("https://github.com/frappe/frappe:version-15")

        assert config.name == "frappe"
        assert config.repo == "https://github.com/frappe/frappe"
        assert config.ref == "version-15"

    def test_parse_https_url_without_ref(self):
        config = AppConfig.from_string("https://github.com/frappe/erpnext")

        assert config.name == "erpnext"
        assert config.repo == "https://github.com/frappe/erpnext"
        assert config.ref is None

    def test_parse_https_url_with_git_extension(self):
        config = AppConfig.from_string("https://github.com/frappe/erpnext.git:develop")

        assert config.name == "erpnext"
        assert config.repo == "https://github.com/frappe/erpnext.git"
        assert config.ref == "develop"

    def test_parse_ssh_url_with_ref(self):
        config = AppConfig.from_string("git@github.com:frappe/frappe:version-15")

        assert config.name == "frappe"
        assert config.repo == "git@github.com:frappe/frappe"
        assert config.ref == "version-15"

    def test_parse_ssh_url_without_ref(self):
        config = AppConfig.from_string("git@github.com:frappe/erpnext")

        assert config.name == "erpnext"
        assert config.repo == "git@github.com:frappe/erpnext"
        assert config.ref is None


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
        app_dict: dict[str, str | None] = {"app": "erpnext", "branch": "version-15"}
        config = AppConfig.from_dict(app_dict)

        assert config.name == "erpnext"
        assert config.repo == "frappe/erpnext"
        assert config.ref == "version-15"

    def test_from_dict_without_branch(self):
        """Test converting dict without branch."""
        app_dict: dict[str, str | None] = {"app": "erpnext", "branch": None}
        config = AppConfig.from_dict(app_dict)

        assert config.name == "erpnext"
        assert config.ref is None

    def test_from_dict_with_token(self):
        """Test converting dict with GitHub token (repo_url should be None to allow fallback)."""
        app_dict: dict[str, str | None] = {"app": "mycompany/private-app", "branch": "main"}
        token = "ghp_test123"
        config = AppConfig.from_dict(app_dict, github_token=token)

        assert config.name == "private-app"
        assert config.repo == "mycompany/private-app"
        assert config.repo_url is None

    def test_from_dict_invalid_empty_app(self):
        """Test that empty app name raises ValueError."""
        app_dict: dict[str, str | None] = {"app": "", "branch": "main"}

        with pytest.raises(ValueError, match="app_dict must contain 'app' key"):
            AppConfig.from_dict(app_dict)

    def test_from_dict_invalid_missing_app(self):
        """Test that missing app key raises ValueError."""
        app_dict: dict[str, str | None] = {"branch": "main"}

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


class TestGetAuthMethodsPrioritization:
    def test_no_token_prioritizes_https_first(self):
        config = AppConfig.from_string("frappe/erpnext:version-15")
        methods = config.get_auth_methods(github_token=None)

        assert len(methods) == 2
        assert methods[0][0] == "HTTPS"
        assert methods[1][0] == "SSH"

    def test_with_token_prioritizes_token_first(self):
        config = AppConfig.from_string("rtcamp/private-app:main")
        methods = config.get_auth_methods(github_token="ghp_test123")

        assert len(methods) == 3
        assert methods[0][0] == "GitHub Token"
        assert methods[1][0] == "HTTPS"
        assert methods[2][0] == "SSH"

    def test_token_url_contains_token(self):
        config = AppConfig.from_string("myorg/repo:main")
        methods = config.get_auth_methods(github_token="ghp_secret")

        token_method = next((m for m in methods if m[0] == "GitHub Token"), None)
        assert token_method is not None
        assert "ghp_secret" in token_method[1]
        assert "https://ghp_secret@github.com" in token_method[1]

    def test_full_url_ignores_token(self):
        config = AppConfig.from_string("https://github.com/frappe/frappe:version-15")
        methods = config.get_auth_methods(github_token="ghp_test123")

        assert len(methods) == 1
        assert methods[0][0] == "Full URL"
        assert "ghp_test123" not in methods[0][1]


class TestAppValidationResult:
    def test_validation_result_success_with_branch(self):
        result = AppValidationResult(
            app_name="erpnext",
            repo="frappe/erpnext",
            ref="version-15",
            success=True,
            auth_method="HTTPS",
            validated_url="https://github.com/frappe/erpnext.git",
        )

        assert result.success is True
        assert result.auth_method == "HTTPS"
        assert "erpnext" in result.display_message
        assert "branch 'version-15'" in result.display_message
        assert "accessible via https" in result.display_message
        assert not result.display_message.startswith("✓")

    def test_validation_result_success_with_commit(self):
        commit_sha = "a" * 40
        result = AppValidationResult(
            app_name="erpnext",
            repo="frappe/erpnext",
            ref=commit_sha,
            success=True,
            auth_method="SSH",
            validated_url="git@github.com:frappe/erpnext.git",
        )

        assert result.success is True
        assert f"commit {commit_sha[:8]}..." in result.display_message
        assert "accessible via ssh" in result.display_message
        assert not result.display_message.startswith("✓")

    def test_validation_result_failure(self):
        result = AppValidationResult(
            app_name="erpnext",
            repo="frappe/erpnext",
            ref="version-15",
            success=False,
            auth_method=None,
            validated_url=None,
            error_message="erpnext: Repository not found",
        )

        assert result.success is False
        assert result.error_message == "erpnext: Repository not found"
        assert result.display_message == "erpnext: Repository not found"
        assert not result.display_message.startswith("❌")


class TestAppBatchValidationResult:
    def test_all_valid_returns_true(self):
        results = [
            AppValidationResult("app1", "org/app1", "main", True, "HTTPS", "url1"),
            AppValidationResult("app2", "org/app2", "dev", True, "SSH", "url2"),
        ]
        batch = AppBatchValidationResult(results)

        assert batch.all_valid is True
        assert batch.success_count == 2
        assert batch.failure_count == 0

    def test_all_valid_returns_false_with_failures(self):
        results = [
            AppValidationResult("app1", "org/app1", "main", True, "HTTPS", "url1"),
            AppValidationResult("app2", "org/app2", "dev", False, None, None, "Error"),
        ]
        batch = AppBatchValidationResult(results)

        assert batch.all_valid is False
        assert batch.success_count == 1
        assert batch.failure_count == 1

    def test_messages_returns_all_display_messages(self):
        results = [
            AppValidationResult("app1", "org/app1", "main", True, "HTTPS", "url1"),
            AppValidationResult("app2", "org/app2", "dev", False, None, None, "❌ app2: Failed"),
        ]
        batch = AppBatchValidationResult(results)

        messages = batch.messages
        assert len(messages) == 2
        assert any("app1" in msg and "https" in msg for msg in messages)
        assert any("app2" in msg and "Failed" in msg for msg in messages)
