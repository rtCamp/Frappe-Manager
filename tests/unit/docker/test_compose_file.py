"""
Unit tests for ComposeFile builder pattern and transaction support.

Tests cover:
- Builder/fluent interface methods (with_*)
- Transaction support (commit, rollback)
- Context manager usage
- Atomic configuration methods (configure_bench, configure_service)
- Auto-save behavior
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
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
                "networks": {"site-network": None}
            },
            "nginx": {
                "image": "nginx:latest",
                "container_name": "nginx",
                "environment": {},
                "labels": {},
                "networks": {"site-network": None}
            }
        },
        "networks": {
            "site-network": {"name": "site-network"}
        },
        "volumes": {
            "frappe-data": {"name": "frappe-data"},
            "nginx-data": {"name": "nginx-data"}
        }
    }


class TestBuilderPatternBasics:
    """Test basic builder pattern functionality."""

    def test_with_envs_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_envs queues changes without applying."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                envs = {"frappe": {"KEY": "value"}}
                result = cf.with_envs(envs)
                
                # Should return self for chaining
                assert result is cf
                
                # Should queue the change
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ('envs', envs, True)
                
                # Should not apply yet
                assert cf.yml['services']['frappe']['environment'] == {}

    def test_with_labels_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_labels queues changes without applying."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                labels = {"frappe": {"traefik.enable": "true"}}
                result = cf.with_labels(labels)
                
                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ('labels', labels)

    def test_with_prefix_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_prefix queues changes."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                result = cf.with_prefix("mysite", "site-network")
                
                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ('prefix', 'mysite', 'site-network')

    def test_with_version_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_version queues changes."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                result = cf.with_version("0.2.0")
                
                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ('version', '0.2.0')

    def test_with_users_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_users queues changes."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                users = {"frappe": {"uid": "1001", "gid": "1001"}}
                result = cf.with_users(users)
                
                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ('users', users)

    def test_with_images_queues_change(self, temp_compose_yml, sample_yml_content):
        """Test that with_images queues changes."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                images = {"frappe": {"image": "frappe:v14"}}
                result = cf.with_images(images)
                
                assert result is cf
                assert len(cf._pending_changes) == 1
                assert cf._pending_changes[0] == ('images', images)


class TestBuilderChaining:
    """Test method chaining with builder pattern."""

    def test_chaining_multiple_methods(self, temp_compose_yml, sample_yml_content):
        """Test chaining multiple with_* methods."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                envs = {"frappe": {"KEY": "value"}}
                labels = {"frappe": {"label": "value"}}
                
                result = cf.with_envs(envs).with_labels(labels).with_version("0.2.0")
                
                # Should return self at the end
                assert result is cf
                
                # Should have queued all changes
                assert len(cf._pending_changes) == 3
                assert cf._pending_changes[0] == ('envs', envs, True)
                assert cf._pending_changes[1] == ('labels', labels)
                assert cf._pending_changes[2] == ('version', '0.2.0')

    def test_chaining_with_commit(self, temp_compose_yml, sample_yml_content):
        """Test chaining ending with commit."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                with patch.object(cf, 'write_to_file') as mock_write:
                    result = cf.with_envs({"frappe": {"KEY": "value"}}).commit()
                    
                    # Should apply changes
                    assert cf.yml['services']['frappe']['environment']['KEY'] == 'value'
                    
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
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                cf._pending_changes = [
                    ('envs', {"frappe": {"KEY1": "value1"}}, True),
                    ('version', '0.2.0')
                ]
                
                with patch.object(cf, 'write_to_file'):
                    cf.commit()
                
                # Changes should be applied
                assert cf.yml['services']['frappe']['environment']['KEY1'] == 'value1'
                assert cf.yml['x-version'] == '0.2.0'
                
                # Queue should be cleared
                assert len(cf._pending_changes) == 0

    def test_commit_writes_to_file(self, temp_compose_yml, sample_yml_content):
        """Test that commit writes to file."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                cf._pending_changes = [('version', '0.2.0')]
                
                with patch.object(cf, 'write_to_file') as mock_write:
                    cf.commit()
                    mock_write.assert_called_once()

    def test_rollback_discards_pending_changes(self, temp_compose_yml, sample_yml_content):
        """Test that rollback discards pending changes."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                original_env = dict(cf.yml['services']['frappe']['environment'])
                
                cf._pending_changes = [
                    ('envs', {"frappe": {"KEY1": "value1"}}, True),
                    ('version', '0.2.0')
                ]
                
                result = cf.rollback()
                
                # Changes should NOT be applied
                assert cf.yml['services']['frappe']['environment'] == original_env
                assert cf.yml['x-version'] == '0.1.0'
                
                # Queue should be cleared
                assert len(cf._pending_changes) == 0
                
                # Should return self
                assert result is cf

    def test_rollback_does_not_write_to_file(self, temp_compose_yml, sample_yml_content):
        """Test that rollback does not write to file."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                cf._pending_changes = [('version', '0.2.0')]
                
                with patch.object(cf, 'write_to_file') as mock_write:
                    cf.rollback()
                    mock_write.assert_not_called()


class TestContextManager:
    """Test context manager functionality."""

    def test_context_manager_saves_on_success(self, temp_compose_yml, sample_yml_content):
        """Test that context manager saves changes on successful exit."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                with patch.object(cf, 'write_to_file') as mock_write:
                    with cf:
                        cf.set_version('0.2.0')
                    
                    # Should save on exit
                    mock_write.assert_called_once()
                    assert cf.yml['x-version'] == '0.2.0'

    def test_context_manager_rollsback_on_error(self, temp_compose_yml, sample_yml_content):
        """Test that context manager rolls back changes on error."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                original_version = cf.yml['x-version']
                
                with patch.object(cf, 'write_to_file') as mock_write:
                    try:
                        with cf:
                            cf.set_version('0.2.0')
                            raise ValueError("Test error")
                    except ValueError:
                        pass
                    
                    # Should NOT save on error
                    mock_write.assert_not_called()
                    
                    # Should rollback changes
                    assert cf.yml['x-version'] == original_version

    def test_context_manager_creates_snapshot(self, temp_compose_yml, sample_yml_content):
        """Test that context manager creates a snapshot on entry."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                assert cf._snapshot is None
                
                with patch.object(cf, 'write_to_file'):
                    with cf:
                        assert cf._snapshot is not None
                        # Snapshot should be a deep copy
                        assert cf._snapshot is not cf.yml


class TestAtomicConfigurationMethods:
    """Test atomic configuration methods."""

    def test_configure_bench_sets_all_values(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench sets all configuration at once."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                envs = {"frappe": {"DB_HOST": "db"}}
                labels = {"frappe": {"label": "value"}}
                users = {"frappe": {"uid": "1001", "gid": "1001"}}
                
                with patch.object(cf, 'write_to_file'):
                    result = cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        envs=envs,
                        labels=labels,
                        users=users,
                        auto_save=False
                    )
                
                # Should set all values
                assert cf.yml['services']['frappe']['environment']['DB_HOST'] == 'db'
                assert cf.yml['services']['frappe']['labels']['label'] == 'value'
                assert cf.yml['services']['frappe']['user'] == '1001:1001'
                assert cf.yml['services']['frappe']['container_name'] == 'mysite__frappe'
                assert cf.yml['x-version'] == '0.2.0'
                
                # Should return self
                assert result is cf

    def test_configure_bench_with_auto_save(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench saves when auto_save=True."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                with patch.object(cf, 'write_to_file') as mock_write:
                    cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        auto_save=True
                    )
                    
                    mock_write.assert_called_once()

    def test_configure_service_sets_all_values(self, temp_compose_yml, sample_yml_content):
        """Test that configure_service sets all service configuration."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                env = {"DB_HOST": "db"}
                labels = {"traefik.enable": "true"}
                
                with patch.object(cf, 'write_to_file'):
                    result = cf.configure_service(
                        service="frappe",
                        env=env,
                        labels=labels,
                        user=("1001", "1001"),
                        auto_save=False
                    )
                
                # Should set all values
                assert cf.yml['services']['frappe']['environment']['DB_HOST'] == 'db'
                assert cf.yml['services']['frappe']['labels']['traefik.enable'] == 'true'
                assert cf.yml['services']['frappe']['user'] == '1001:1001'
                
                # Should return self
                assert result is cf

    def test_configure_bench_with_tuple_users(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench handles tuple user format."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                # Use tuple format for users
                users = {"frappe": (1000, 100), "nginx": (1001, 101)}
                
                with patch.object(cf, 'write_to_file'):
                    cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        users=users,
                        auto_save=False
                    )
                
                # Should convert tuples to dict format and set correctly
                assert cf.yml['services']['frappe']['user'] == '1000:100'
                assert cf.yml['services']['nginx']['user'] == '1001:101'

    def test_configure_bench_with_dict_users(self, temp_compose_yml, sample_yml_content):
        """Test that configure_bench handles dict user format."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                # Use dict format for users
                users = {"frappe": {"uid": "1000", "gid": "100"}}
                
                with patch.object(cf, 'write_to_file'):
                    cf.configure_bench(
                        prefix="mysite",
                        version="0.2.0",
                        users=users,
                        auto_save=False
                    )
                
                # Should handle dict format correctly
                assert cf.yml['services']['frappe']['user'] == '1000:100'


class TestAutoSaveBehavior:
    """Test auto-save parameter behavior."""

    def test_auto_save_true_by_default(self, temp_compose_yml, sample_yml_content):
        """Test that auto_save defaults to True."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml)
                
                assert cf._auto_save is True

    def test_auto_save_false_when_specified(self, temp_compose_yml, sample_yml_content):
        """Test that auto_save can be set to False."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                assert cf._auto_save is False


class TestApplyChange:
    """Test the _apply_change internal method."""

    def test_apply_envs_change(self, temp_compose_yml, sample_yml_content):
        """Test applying envs change."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                change = ('envs', {"frappe": {"KEY": "value"}}, True)
                cf._apply_change(change)
                
                assert cf.yml['services']['frappe']['environment']['KEY'] == 'value'

    def test_apply_labels_change(self, temp_compose_yml, sample_yml_content):
        """Test applying labels change."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                change = ('labels', {"frappe": {"label": "value"}})
                cf._apply_change(change)
                
                assert cf.yml['services']['frappe']['labels']['label'] == 'value'

    def test_apply_version_change(self, temp_compose_yml, sample_yml_content):
        """Test applying version change."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                change = ('version', '0.2.0')
                cf._apply_change(change)
                
                assert cf.yml['x-version'] == '0.2.0'

    def test_apply_prefix_change(self, temp_compose_yml, sample_yml_content):
        """Test applying prefix change."""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                change = ('prefix', 'mysite', 'site-network')
                cf._apply_change(change)
                
                assert cf.yml['services']['frappe']['container_name'] == 'mysite__frappe'


class TestMigrateImages:
    """Test migrate_images() method for batch image updates"""
    
    def test_migrate_images_updates_tags(self, temp_compose_yml, sample_yml_content):
        """Test that migrate_images updates multiple image tags"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                tag_updates = {
                    'frappe': 'v14',
                    'nginx': 'v1.21'
                }
                
                result = cf.migrate_images(tag_updates, auto_save=False)
                
                assert result is cf  # Returns self
                assert cf.yml['services']['frappe']['image'] == 'frappe:v14'
                assert cf.yml['services']['nginx']['image'] == 'nginx:v1.21'
    
    def test_migrate_images_with_version_update(self, temp_compose_yml, sample_yml_content):
        """Test that migrate_images can also update compose version"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                tag_updates = {'frappe': 'v15'}
                
                cf.migrate_images(tag_updates, new_version='0.2.0', auto_save=False)
                
                assert cf.yml['services']['frappe']['image'] == 'frappe:v15'
                assert cf.yml['x-version'] == '0.2.0'
    
    def test_migrate_images_auto_save(self, temp_compose_yml, sample_yml_content):
        """Test that migrate_images auto-saves when auto_save=True"""
        with patch('builtins.open', mock_open()) as mock_file:
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                cf.write_to_file = MagicMock()
                
                cf.migrate_images({'frappe': 'v14'}, auto_save=True)
                
                cf.write_to_file.assert_called_once()
    
    def test_migrate_images_skips_missing_services(self, temp_compose_yml, sample_yml_content):
        """Test that migrate_images skips services not in compose file"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                tag_updates = {
                    'frappe': 'v14',
                    'nonexistent': 'v1.0'
                }
                
                # Should not raise error
                cf.migrate_images(tag_updates, auto_save=False)
                
                assert cf.yml['services']['frappe']['image'] == 'frappe:v14'


class TestUpdateHelperMethods:
    """Test update helper methods (update_env, delete_env, update_label, update_image_tag)"""
    
    def test_update_env_adds_new_variable(self, temp_compose_yml, sample_yml_content):
        """Test that update_env adds a new environment variable"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                result = cf.update_env('frappe', 'NEW_VAR', 'new_value', auto_save=False)
                
                assert result is cf  # Returns self
                assert cf.yml['services']['frappe']['environment']['NEW_VAR'] == 'new_value'
    
    def test_update_env_updates_existing_variable(self, temp_compose_yml, sample_yml_content):
        """Test that update_env updates an existing environment variable"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                cf.yml['services']['frappe']['environment']['EXISTING'] = 'old_value'
                
                cf.update_env('frappe', 'EXISTING', 'updated_value', auto_save=False)
                
                assert cf.yml['services']['frappe']['environment']['EXISTING'] == 'updated_value'
    
    def test_update_env_creates_environment_dict(self, temp_compose_yml, sample_yml_content):
        """Test that update_env creates environment dict if missing"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                del cf.yml['services']['frappe']['environment']
                
                cf.update_env('frappe', 'VAR', 'value', auto_save=False)
                
                assert 'environment' in cf.yml['services']['frappe']
                assert cf.yml['services']['frappe']['environment']['VAR'] == 'value'
    
    def test_update_env_auto_save(self, temp_compose_yml, sample_yml_content):
        """Test that update_env respects auto_save parameter"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                cf.write_to_file = MagicMock()
                
                cf.update_env('frappe', 'VAR', 'value', auto_save=True)
                
                cf.write_to_file.assert_called_once()
    
    def test_delete_env_removes_variable(self, temp_compose_yml, sample_yml_content):
        """Test that delete_env removes an environment variable"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                cf.yml['services']['frappe']['environment']['TO_DELETE'] = 'value'
                
                result = cf.delete_env('frappe', 'TO_DELETE', auto_save=False)
                
                assert result is cf  # Returns self
                assert 'TO_DELETE' not in cf.yml['services']['frappe']['environment']
    
    def test_delete_env_handles_missing_key(self, temp_compose_yml, sample_yml_content):
        """Test that delete_env handles missing keys gracefully"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                # Should not raise error
                cf.delete_env('frappe', 'NONEXISTENT', auto_save=False)
    
    def test_update_label_adds_new_label(self, temp_compose_yml, sample_yml_content):
        """Test that update_label adds a new label"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                result = cf.update_label('frappe', 'app.version', 'v14', auto_save=False)
                
                assert result is cf  # Returns self
                assert cf.yml['services']['frappe']['labels']['app.version'] == 'v14'
    
    def test_update_label_creates_labels_dict(self, temp_compose_yml, sample_yml_content):
        """Test that update_label creates labels dict if missing"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                del cf.yml['services']['frappe']['labels']
                
                cf.update_label('frappe', 'new.label', 'value', auto_save=False)
                
                assert 'labels' in cf.yml['services']['frappe']
                assert cf.yml['services']['frappe']['labels']['new.label'] == 'value'
    
    def test_update_image_tag_changes_tag(self, temp_compose_yml, sample_yml_content):
        """Test that update_image_tag updates the image tag"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                result = cf.update_image_tag('frappe', 'v15', auto_save=False)
                
                assert result is cf  # Returns self
                assert cf.yml['services']['frappe']['image'] == 'frappe:v15'
    
    def test_update_image_tag_preserves_image_name(self, temp_compose_yml, sample_yml_content):
        """Test that update_image_tag preserves the image name"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                cf.yml['services']['frappe']['image'] = 'frappe/frappe-socketio:v13'
                
                cf.update_image_tag('frappe', 'v14', auto_save=False)
                
                assert cf.yml['services']['frappe']['image'] == 'frappe/frappe-socketio:v14'
    
    def test_update_helper_methods_chaining(self, temp_compose_yml, sample_yml_content):
        """Test that helper methods can be chained"""
        with patch('builtins.open', mock_open()):
            with patch('frappe_manager.docker.compose_file.yaml.load', return_value=sample_yml_content):
                cf = ComposeFile(temp_compose_yml, auto_save=False)
                
                cf.update_env('frappe', 'VAR1', 'value1', auto_save=False) \
                  .update_env('frappe', 'VAR2', 'value2', auto_save=False) \
                  .update_label('frappe', 'label1', 'val1', auto_save=False) \
                  .update_image_tag('frappe', 'v14', auto_save=False)
                
                assert cf.yml['services']['frappe']['environment']['VAR1'] == 'value1'
                assert cf.yml['services']['frappe']['environment']['VAR2'] == 'value2'
                assert cf.yml['services']['frappe']['labels']['label1'] == 'val1'
                assert cf.yml['services']['frappe']['image'] == 'frappe:v14'


class TestStaticTemplateMethods:
    """Test static template access methods (load_template_yml, get_template_images, get_template_services)"""
    
    def test_load_template_yml_returns_dict(self, tmp_path):
        """Test that load_template_yml returns parsed YAML"""
        template_yml = """
version: '3'
services:
  web:
    image: nginx:latest
  db:
    image: postgres:14
"""
        template_file = tmp_path / "test-template.yml"
        template_file.write_text(template_yml)
        
        with patch('frappe_manager.docker.compose_file.get_template_path', return_value=template_file):
            yml = ComposeFile.load_template_yml('test-template.yml', str(tmp_path))
            
            assert isinstance(yml, dict)
            assert 'services' in yml
            assert 'web' in yml['services']
            assert 'db' in yml['services']
    
    def test_get_template_images_extracts_images(self, tmp_path):
        """Test that get_template_images extracts all images"""
        template_yml = """
version: '3'
services:
  web:
    image: nginx:1.21
  db:
    image: postgres:14
  app:
    image: myapp
"""
        template_file = tmp_path / "test-template.yml"
        template_file.write_text(template_yml)
        
        with patch('frappe_manager.docker.compose_file.get_template_path', return_value=template_file):
            images = ComposeFile.get_template_images('test-template.yml', str(tmp_path))
            
            assert len(images) == 3
            assert images['web']['name'] == 'nginx'
            assert images['web']['tag'] == '1.21'
            assert images['db']['name'] == 'postgres'
            assert images['db']['tag'] == '14'
            assert images['app']['name'] == 'myapp'
            assert images['app']['tag'] == 'latest'  # Default tag
    
    def test_get_template_services_returns_list(self, tmp_path):
        """Test that get_template_services returns service names"""
        template_yml = """
version: '3'
services:
  web:
    image: nginx:latest
  db:
    image: postgres:14
  cache:
    image: redis:6
"""
        template_file = tmp_path / "test-template.yml"
        template_file.write_text(template_yml)
        
        with patch('frappe_manager.docker.compose_file.get_template_path', return_value=template_file):
            services = ComposeFile.get_template_services('test-template.yml', str(tmp_path))
            
            assert isinstance(services, list)
            assert len(services) == 3
            assert 'web' in services
            assert 'db' in services
            assert 'cache' in services
    
    def test_static_methods_are_classmethods(self):
        """Test that template methods can be called without instantiation"""
        # Should not raise error - can call on class
        assert callable(ComposeFile.load_template_yml)
        assert callable(ComposeFile.get_template_images)
        assert callable(ComposeFile.get_template_services)
