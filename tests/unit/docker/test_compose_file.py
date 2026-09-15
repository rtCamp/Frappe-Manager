"""
Unit tests for ComposeFile builder pattern and transaction support.

Tests cover:
- Builder/fluent interface methods (with_*)
- Transaction support (commit, rollback)
- Context manager usage
- Atomic configuration methods (configure_bench, configure_service)
- Auto-save behavior
"""

from unittest.mock import mock_open, patch

import pytest
from ruamel.yaml.comments import CommentedMap as OrderedDict

from frappe_manager.docker import ComposeFile


@pytest.fixture
def temp_compose_yml(tmp_path):
    """Create a temporary compose file path."""
    return tmp_path / "docker-compose.yml"


@pytest.fixture
def sample_yml_content():
    """Sample docker-compose YAML structure."""
    return {
        "version": "3",
        "x-version": "0.1.0",
        "services": {
            "frappe": {
                "image": "frappe:latest",
                "container_name": "frappe",
                "environment": {},
                "labels": {},
                "user": "1000:1000",
                "networks": {"site-network": None},
            },
            "nginx": {
                "image": "nginx:latest",
                "container_name": "nginx",
                "environment": {},
                "labels": {},
                "networks": {"site-network": None},
            },
        },
        "networks": {
            "site-network": {"name": "site-network"},
        },
        "volumes": {
            "frappe-data": {"name": "frappe-data"},
            "nginx-data": {"name": "nginx-data"},
        },
    }


class TestBuilderPatternBasics:
    """Test basic builder pattern functionality."""

    def test_with_envs_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_envs queues changes without applying."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                envs = {"frappe": {"KEY": "value"}}
                result = cf.with_envs(envs)

                # Should return self for chaining
                assert result is cf

                # Should queue the change
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ("envs", envs, True)

                # Should not apply yet
                assert cf.yml["services"]["frappe"]["environment"] == {}

    def test_with_labels_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_labels queues changes without applying."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                labels = {"frappe": {"traefik.enable": "true"}}
                result = cf.with_labels(labels)

                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ("labels", labels)

    def test_with_prefix_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_prefix queues changes."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                result = cf.with_prefix("mysite", "site-network")

                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ("prefix", "mysite", "site-network")

    def test_with_version_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_version queues changes."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                result = cf.with_version("0.2.0")

                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ("version", "0.2.0")

    def test_with_users_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_users queues changes."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                users = {"frappe": {"uid": "1001", "gid": "1001"}}
                result = cf.with_users(users)

                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ("users", users)


class TestBuilderChaining:
    """Test method chaining with builder pattern."""

    def test_chaining_multiple_methods(self, temp_compose_yml, sample_yml_content):
        """Test chaining multiple with_* methods."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                envs = {"frappe": {"KEY": "value"}}
                labels = {"frappe": {"label": "value"}}

                result = cf.with_envs(envs).with_labels(labels).with_version("0.2.0")

                # Should return self at the end
                assert result is cf

                # Should have queued all changes
                assert len(cf._pending_changes) == 3
                assert cf._pending_changes[0] == ("envs", envs, True)
                assert cf._pending_changes[1] == ("labels", labels)
                assert cf._pending_changes[2] == ("version", "0.2.0")

    def test_chaining_with_commit(self, temp_compose_yml, sample_yml_content):
        """Test chaining ending with commit."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                with patch.object(cf, "write_to_file") as mock_write:
                    result = cf.with_envs({"frappe": {"KEY": "value"}}).commit()

                    # Should apply changes
                    assert cf.yml["services"]["frappe"]["environment"]["KEY"] == "value"

                    # Should clear pending changes
                    assert len(cf._pending_changes) == 0

                    # Should write to file
                    mock_write.assert_called_once()

                    # Should return self
                    assert result is cf


class TestTransactionSupport:
    """Test transaction support (commit/rollback)."""

    def test_commit_applies_pending_changes(self, temp_compose_yml, sample_yml_content):
        """Test that commit applies all pending changes."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                cf._pending_changes = [
                    ("envs", {"frappe": {"KEY1": "value1"}}, True),
                    ("version", "0.2.0"),
                ]

                with patch.object(cf, "write_to_file"):
                    cf.commit()

                # Changes should be applied
                assert cf.yml["services"]["frappe"]["environment"]["KEY1"] == "value1"
                assert cf.yml["x-version"] == "0.2.0"

                # Queue should be cleared
                assert len(cf._pending_changes) == 0

    def test_commit_writes_to_file(self, temp_compose_yml, sample_yml_content):
        """Test that commit writes to file."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                cf._pending_changes = [("version", "0.2.0")]

                with patch.object(cf, "write_to_file") as mock_write:
                    cf.commit()
                    mock_write.assert_called_once()

    def test_rollback_discards_pending_changes(self, temp_compose_yml, sample_yml_content):
        """Test that rollback discards pending changes."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                original_env = dict(cf.yml["services"]["frappe"]["environment"])

                cf._pending_changes = [
                    ("envs", {"frappe": {"KEY1": "value1"}}, True),
                    ("version", "0.2.0"),
                ]

                result = cf.rollback()

                # Changes should NOT be applied
                assert cf.yml["services"]["frappe"]["environment"] == original_env
                assert cf.yml["x-version"] == "0.1.0"

                # Queue should be cleared
                assert len(cf._pending_changes) == 0

                # Should return self
                assert result is cf

    def test_rollback_does_not_write_to_file(self, temp_compose_yml, sample_yml_content):
        """Test that rollback does not write to file."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                cf._pending_changes = [("version", "0.2.0")]

                with patch.object(cf, "write_to_file") as mock_write:
                    cf.rollback()
                    mock_write.assert_not_called()


class TestContextManager:
    """Test context manager functionality."""

    def test_context_manager_saves_on_success(self, temp_compose_yml, sample_yml_content):
        """Test that context manager saves changes on successful exit."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                with patch.object(cf, "write_to_file") as mock_write:
                    with cf:
                        cf.set_version("0.2.0")

                    # Should save on exit
                    mock_write.assert_called_once()
                    assert cf.yml["x-version"] == "0.2.0"

    def test_context_manager_rollsback_on_error(self, temp_compose_yml, sample_yml_content):
        """Test that context manager rolls back changes on error."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                original_version = cf.yml["x-version"]

                with patch.object(cf, "write_to_file") as mock_write:
                    try:
                        with cf:
                            cf.set_version("0.2.0")
                            raise ValueError("Test error")
                    except ValueError:
                        pass

                    # Should NOT save on error
                    mock_write.assert_not_called()

                    # Should rollback changes
                    assert cf.yml["x-version"] == original_version

    def test_context_manager_creates_snapshot(self, temp_compose_yml, sample_yml_content):
        """Test that context manager creates a snapshot on entry."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                assert cf._snapshot is None

                with patch.object(cf, "write_to_file"), cf:
                    assert cf._snapshot is not None
                    # Snapshot should be a deep copy
                    assert cf._snapshot is not cf.yml


class TestAtomicConfigurationMethods:
    """Test atomic configuration methods."""

    def test_configure_bench_sets_all_values(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench sets all configuration at once."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                envs = {"frappe": {"DB_HOST": "db"}}
                labels = {"frappe": {"label": "value"}}
                users = {"frappe": {"uid": "1001", "gid": "1001"}}

                with patch.object(cf, "write_to_file"):
                    result = cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        envs=envs,
                        labels=labels,
                        users=users,
                        auto_save=False,
                    )

                # Should set all values
                assert cf.yml["services"]["frappe"]["environment"]["DB_HOST"] == "db"
                assert cf.yml["services"]["frappe"]["labels"]["label"] == "value"
                assert cf.yml["services"]["frappe"]["user"] == "1001:1001"
                assert cf.yml["services"]["frappe"]["container_name"] == "mysite__frappe"
                assert cf.yml["x-version"] == "0.2.0"

                # Should return self
                assert result is cf

    def test_configure_bench_with_auto_save(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench saves when auto_save=True."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                with patch.object(cf, "write_to_file") as mock_write:
                    cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        auto_save=True,
                    )

                    mock_write.assert_called_once()

    def test_configure_service_sets_all_values(self, temp_compose_yml, sample_yml_content):
        """Test that configure_service sets all service configuration."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                env = {"DB_HOST": "db"}
                labels = {"traefik.enable": "true"}

                with patch.object(cf, "write_to_file"):
                    result = cf.configure_service(
                        service="frappe",
                        env=env,
                        labels=labels,
                        user=("1001", "1001"),
                        auto_save=False,
                    )

                # Should set all values
                assert cf.yml["services"]["frappe"]["environment"]["DB_HOST"] == "db"
                assert cf.yml["services"]["frappe"]["labels"]["traefik.enable"] == "true"
                assert cf.yml["services"]["frappe"]["user"] == "1001:1001"

                # Should return self
                assert result is cf

    def test_configure_bench_with_tuple_users(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench handles tuple user format."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                # Use tuple format for users
                users = {"frappe": (1000, 100), "nginx": (1001, 101)}

                with patch.object(cf, "write_to_file"):
                    cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        users=users,
                        auto_save=False,
                    )

                # Should convert tuples to dict format and set correctly
                assert cf.yml["services"]["frappe"]["user"] == "1000:100"
                assert cf.yml["services"]["nginx"]["user"] == "1001:101"

    def test_configure_bench_with_dict_users(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench handles dict user format."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                # Use dict format for users
                users = {"frappe": {"uid": "1000", "gid": "100"}}

                with patch.object(cf, "write_to_file"):
                    cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        users=users,
                        auto_save=False,
                    )

                # Should handle dict format correctly
                assert cf.yml["services"]["frappe"]["user"] == "1000:100"


class TestApplyChange:
    """Test the _apply_change internal method."""

    def test_apply_envs_change(self, temp_compose_yml, sample_yml_content):
        """Test applying envs change."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                change = ("envs", {"frappe": {"KEY": "value"}}, True)
                cf._apply_change(change)

                assert cf.yml["services"]["frappe"]["environment"]["KEY"] == "value"

    def test_apply_labels_change(self, temp_compose_yml, sample_yml_content):
        """Test applying labels change."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                change = ("labels", {"frappe": {"label": "value"}})
                cf._apply_change(change)

                assert cf.yml["services"]["frappe"]["labels"]["label"] == "value"

    def test_apply_version_change(self, temp_compose_yml, sample_yml_content):
        """Test applying version change."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                change = ("version", "0.2.0")
                cf._apply_change(change)

                assert cf.yml["x-version"] == "0.2.0"

    def test_apply_prefix_change(self, temp_compose_yml, sample_yml_content):
        """Test applying prefix change."""
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)

                change = ("prefix", "mysite", "site-network")
                cf._apply_change(change)

                assert cf.yml["services"]["frappe"]["container_name"] == "mysite__frappe"


class TestGetAllImages:
    """get_all_images decomposes each service's image via ImageRef (#digest-refs): a registry
    host:port, a bare digest pin, and a combined tag+digest reference must all be reported
    correctly, not just the plain repo:tag shape every other test here uses."""

    def test_registry_port_is_not_mistaken_for_a_second_colon_split(self, temp_compose_yml):
        """`image.split(":")` used to raise ValueError on `localhost:5000/app:v1` (3 parts, 2
        colons) reached whenever an image-runtime bench is switched to a host:port registry."""
        sample = {"services": {"frappe": {"image": "localhost:5000/app:v1"}}}
        with (
            patch("builtins.open", mock_open()),
            patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample),
        ):
            cf = ComposeFile(temp_compose_yml)
            images = cf.get_all_images()

        assert images["frappe"] == {
            "name": "localhost:5000/app",
            "tag": "v1",
            "digest": None,
            "image": "localhost:5000/app:v1",
        }

    def test_digest_pinned_image_reports_the_digest_not_a_fake_tag(self, temp_compose_yml):
        """A digest's colon used to be mistaken for a tag's (`app@sha256:aaaa` -> name
        `app@sha256`, tag `aaaa`); reachable via a mount-runtime `base_image` pin."""
        sample = {"services": {"frappe": {"image": "app@sha256:aaaa"}}}
        with (
            patch("builtins.open", mock_open()),
            patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample),
        ):
            cf = ComposeFile(temp_compose_yml)
            images = cf.get_all_images()

        assert images["frappe"] == {"name": "app", "tag": None, "digest": "sha256:aaaa", "image": "app@sha256:aaaa"}

    def test_tag_and_digest_together_do_not_crash(self, temp_compose_yml):
        sample = {"services": {"frappe": {"image": "ghcr.io/acme/app:v1@sha256:aaaa"}}}
        with (
            patch("builtins.open", mock_open()),
            patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample),
        ):
            cf = ComposeFile(temp_compose_yml)
            images = cf.get_all_images()

        assert images["frappe"] == {
            "name": "ghcr.io/acme/app",
            "tag": "v1",
            "digest": "sha256:aaaa",
            "image": "ghcr.io/acme/app:v1@sha256:aaaa",
        }

    def test_untagged_image_still_defaults_to_latest(self, temp_compose_yml, sample_yml_content):
        """A floating bare repo has no explicit tag, but docker itself resolves it to `latest`,
        so the report keeps saying so -- existing callers compare against that literal."""
        sample_yml_content["services"]["frappe"]["image"] = "frappe"
        with (
            patch("builtins.open", mock_open()),
            patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content),
        ):
            cf = ComposeFile(temp_compose_yml)
            images = cf.get_all_images()

        assert images["frappe"] == {"name": "frappe", "tag": "latest", "digest": None, "image": "frappe"}


class TestSetAllImages:
    """set_all_images reconstructs the compose "image:" string from name/tag/digest -- the
    inverse of get_all_images/apply_specs, so a digest must round-trip and a caller that only
    ever supplied name/tag (every caller before #digest-refs) must see no behaviour change."""

    def test_legacy_name_and_tag_only_shape_is_unchanged(self, temp_compose_yml, sample_yml_content):
        with (
            patch("builtins.open", mock_open()),
            patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content),
        ):
            cf = ComposeFile(temp_compose_yml)
            cf.set_all_images({"frappe": {"name": "frappe", "tag": "v9"}})

        assert cf.yml["services"]["frappe"]["image"] == "frappe:v9"

    def test_digest_only_is_written_without_a_bogus_tag(self, temp_compose_yml, sample_yml_content):
        with (
            patch("builtins.open", mock_open()),
            patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content),
        ):
            cf = ComposeFile(temp_compose_yml)
            cf.set_all_images({"frappe": {"name": "app", "tag": None, "digest": "sha256:aaaa"}})

        assert cf.yml["services"]["frappe"]["image"] == "app@sha256:aaaa"

    def test_tag_and_digest_together_are_both_written(self, temp_compose_yml, sample_yml_content):
        with (
            patch("builtins.open", mock_open()),
            patch("frappe_manager.docker.compose_file.yaml.load", return_value=sample_yml_content),
        ):
            cf = ComposeFile(temp_compose_yml)
            cf.set_all_images({"frappe": {"name": "app", "tag": "v1", "digest": "sha256:aaaa"}})

        assert cf.yml["services"]["frappe"]["image"] == "app:v1@sha256:aaaa"


class TestServiceProfileDisabled:
    """Tests for is_service_profile_disabled and get_services_list(exclude_disabled=True)."""

    def _make_cf(self, temp_compose_yml, yml_content):
        with patch("builtins.open", mock_open()):
            with patch("frappe_manager.docker.compose_file.yaml.load", return_value=yml_content):
                return ComposeFile(temp_compose_yml)

    def test_disabled_profile_as_list(self, temp_compose_yml, sample_yml_content):
        sample_yml_content["services"]["frappe"]["profiles"] = ["disabled"]
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        assert cf.is_service_profile_disabled("frappe") is True

    def test_disabled_profile_as_string(self, temp_compose_yml, sample_yml_content):
        sample_yml_content["services"]["frappe"]["profiles"] = "disabled"
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        assert cf.is_service_profile_disabled("frappe") is True

    def test_non_disabled_profile_string_no_false_positive(self, temp_compose_yml, sample_yml_content):
        sample_yml_content["services"]["frappe"]["profiles"] = "notdisabled"
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        assert cf.is_service_profile_disabled("frappe") is False

    def test_disabled_in_list_with_other_profiles(self, temp_compose_yml, sample_yml_content):
        sample_yml_content["services"]["frappe"]["profiles"] = ["web", "disabled"]
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        assert cf.is_service_profile_disabled("frappe") is True

    def test_no_profiles_key(self, temp_compose_yml, sample_yml_content):
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        assert cf.is_service_profile_disabled("frappe") is False

    def test_nonexistent_service(self, temp_compose_yml, sample_yml_content):
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        assert cf.is_service_profile_disabled("nonexistent") is False

    def test_get_services_list_exclude_disabled_filters_service(self, temp_compose_yml, sample_yml_content):
        sample_yml_content["services"]["frappe"]["profiles"] = ["disabled"]
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        services = cf.get_services_list(exclude_disabled=True)
        assert "frappe" not in services
        assert "nginx" in services

    def test_get_services_list_exclude_disabled_false_returns_all(self, temp_compose_yml, sample_yml_content):
        sample_yml_content["services"]["frappe"]["profiles"] = ["disabled"]
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        services = cf.get_services_list(exclude_disabled=False)
        assert "frappe" in services
        assert "nginx" in services

    def test_get_services_list_no_disabled_services(self, temp_compose_yml, sample_yml_content):
        cf = self._make_cf(temp_compose_yml, sample_yml_content)
        services = cf.get_services_list(exclude_disabled=True)
        assert services == list(sample_yml_content["services"].keys())


class TestSetEnvsAppend:
    """`set_envs(..., append=True)` MERGES onto the existing environment; append=False replaces it.

    The merge is gated on the value being a plain ``dict`` (``type(env) == dict``), because a
    ruamel ``CommentedMap`` read back out of the yaml carries anchors/comments that must not be
    folded into a caller-supplied mapping. Both halves of that gate are pinned here: losing the
    merge silently drops every previously configured variable, which is how a bench ends up
    booting without its DB credentials.
    """

    @pytest.fixture
    def cf(self, tmp_path):
        """Real compose file on disk, so `environment` is the ruamel type production sees."""
        path = tmp_path / "docker-compose.yml"
        path.write_text(
            "version: '3'\n"
            "services:\n"
            "  frappe:\n"
            "    image: frappe:latest\n"
            "    environment:\n"
            "      DB_HOST: mariadb\n"
            "      DB_NAME: sitedb\n",
        )
        return ComposeFile(loadfile=path)

    def test_append_dict_merges_with_existing_envs(self, cf):
        cf.set_envs("frappe", {"REDIS_CACHE": "redis-cache:6379"}, append=True)

        assert dict(cf.get_envs("frappe")) == {
            "DB_HOST": "mariadb",
            "DB_NAME": "sitedb",
            "REDIS_CACHE": "redis-cache:6379",
        }

    def test_append_dict_overrides_only_the_keys_supplied(self, cf):
        cf.set_envs("frappe", {"DB_HOST": "external-db"}, append=True)

        envs = dict(cf.get_envs("frappe"))
        assert envs["DB_HOST"] == "external-db"
        assert envs["DB_NAME"] == "sitedb"

    def test_append_false_replaces_existing_envs(self, cf):
        cf.set_envs("frappe", {"REDIS_CACHE": "redis-cache:6379"})

        assert dict(cf.get_envs("frappe")) == {"REDIS_CACHE": "redis-cache:6379"}

    def test_append_of_non_plain_dict_replaces_instead_of_merging(self, cf):
        """A CommentedMap is not `type(env) == dict`, so the merge is skipped even with append=True.

        Pinned as-is (see class docstring): a subclass of dict does NOT append today.
        """
        cf.set_envs("frappe", OrderedDict({"REDIS_CACHE": "redis-cache:6379"}), append=True)

        assert dict(cf.get_envs("frappe")) == {"REDIS_CACHE": "redis-cache:6379"}

    def test_append_onto_service_without_environment_key(self, tmp_path):
        path = tmp_path / "docker-compose.yml"
        path.write_text("version: '3'\nservices:\n  frappe:\n    image: frappe:latest\n")
        cf = ComposeFile(loadfile=path)

        cf.set_envs("frappe", {"DB_HOST": "mariadb"}, append=True)

        assert dict(cf.get_envs("frappe")) == {"DB_HOST": "mariadb"}
