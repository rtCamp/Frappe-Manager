"""
Tests for frappe_manager.ssl_manager.proxy_storage module.

This module tests the ProxyStoragePaths class which reads and exposes
nginx-proxy volume mount configurations from Docker Compose projects.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock
from frappe_manager.ssl_manager.proxy_storage import ProxyStoragePaths
from frappe_manager.docker import DockerVolumeMount, DockerVolumeType


class TestProxyStoragePathsInitialization:
    """Tests for ProxyStoragePaths initialization."""

    def test_init_stores_service_name_and_compose_project(self, mocker, mock_compose_file_manager):
        """Test that initialization stores service name and compose file manager."""
        # Mock the volume retrieval
        mock_compose_file_manager.get_service_volumes.return_value = []

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        assert storage.service_name == 'nginx-proxy'
        assert storage.compose_file_manager == mock_compose_file_manager

    def test_init_calls_get_docker_volume_dirs(self, mocker, mock_compose_file_manager):
        """Test that initialization calls _get_docker_volume_dirs."""
        mock_compose_file_manager.get_service_volumes.return_value = []

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # Verify get_service_volumes was called
        mock_compose_file_manager.get_service_volumes.assert_called_once_with('nginx-proxy')

    def test_init_sets_dirs_attribute(self, mocker, mock_compose_file_manager):
        """Test that initialization sets the dirs attribute."""
        mock_compose_file_manager.get_service_volumes.return_value = []

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        assert hasattr(storage, 'dirs')
        assert storage.dirs is not None


class TestProxyStoragePathsGetDockerVolumeDirs:
    """Tests for ProxyStoragePaths._get_docker_volume_dirs method."""

    def test_get_docker_volume_dirs_with_bind_volumes(self, mocker, mock_compose_file_manager):
        """Test that bind volumes are correctly extracted and exposed."""
        # Create mock bind volume
        compose_path = Path('/app/docker-compose.yml')
        mock_volume = DockerVolumeMount(
            host='/var/lib/nginx/certs',
            container='/etc/nginx/certs',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )
        mock_compose_file_manager.get_service_volumes.return_value = [mock_volume]

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # Check that dirs has the 'certs' attribute (based on directory name)
        assert hasattr(storage.dirs, 'certs')
        assert storage.dirs.certs == mock_volume

    def test_get_docker_volume_dirs_filters_non_bind_volumes(self, mocker, mock_compose_file_manager):
        """Test that non-bind volumes (like named volumes) are filtered out."""
        compose_path = Path('/app/docker-compose.yml')

        bind_volume = DockerVolumeMount(
            host='/var/lib/nginx/certs',
            container='/etc/nginx/certs',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        named_volume = DockerVolumeMount(
            host='nginx-data', container='/data', type=DockerVolumeType.volume, compose_path=compose_path
        )

        mock_compose_file_manager.get_service_volumes.return_value = [bind_volume, named_volume]

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # Only bind volume should be present
        assert hasattr(storage.dirs, 'certs')
        assert not hasattr(storage.dirs, 'nginx-data')

    def test_get_docker_volume_dirs_with_multiple_bind_volumes(self, mocker, mock_compose_file_manager):
        """Test handling multiple bind volumes."""
        compose_path = Path('/app/docker-compose.yml')

        volume1 = DockerVolumeMount(
            host='/var/lib/nginx/certs',
            container='/etc/nginx/certs',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        volume2 = DockerVolumeMount(
            host='/var/lib/nginx/conf.d',
            container='/etc/nginx/conf.d',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        volume3 = DockerVolumeMount(
            host='/var/lib/nginx/html',
            container='/usr/share/nginx/html',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        mock_compose_file_manager.get_service_volumes.return_value = [volume1, volume2, volume3]

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # All bind volumes should be accessible
        assert hasattr(storage.dirs, 'certs')
        assert hasattr(storage.dirs, 'conf.d')
        assert hasattr(storage.dirs, 'html')
        assert storage.dirs.certs == volume1
        assert getattr(storage.dirs, 'conf.d') == volume2
        assert storage.dirs.html == volume3

    def test_get_docker_volume_dirs_with_no_volumes(self, mocker, mock_compose_file_manager):
        """Test handling when no volumes are configured."""
        mock_compose_file_manager.get_service_volumes.return_value = []

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # dirs should exist but have no attributes
        assert hasattr(storage, 'dirs')
        # Check that dirs has no volume attributes (only has the default __module__, etc.)
        dir_attrs = [attr for attr in dir(storage.dirs) if not attr.startswith('_')]
        assert len(dir_attrs) == 0

    def test_get_docker_volume_dirs_uses_host_path_name(self, mocker, mock_compose_file_manager):
        """Test that directory name is derived from host path's basename."""
        compose_path = Path('/app/docker-compose.yml')

        mock_volume = DockerVolumeMount(
            host='/very/long/nested/path/to/ssl-certs',
            container='/etc/nginx/certs',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        mock_compose_file_manager.get_service_volumes.return_value = [mock_volume]

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # Attribute should be named after the last component of the host path
        assert hasattr(storage.dirs, 'ssl-certs')
        assert getattr(storage.dirs, 'ssl-certs') == mock_volume


class TestProxyStoragePathsVolumeAccess:
    """Tests for accessing volume information through ProxyStoragePaths."""

    def test_volume_mount_host_path_accessible(self, mocker, mock_compose_file_manager):
        """Test that host paths are accessible through the dirs object."""
        compose_path = Path('/app/docker-compose.yml')

        mock_volume = DockerVolumeMount(
            host='/var/lib/nginx/certs',
            container='/etc/nginx/certs',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        mock_compose_file_manager.get_service_volumes.return_value = [mock_volume]

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # Host should be a Path object
        assert isinstance(storage.dirs.certs.host, Path)
        assert storage.dirs.certs.host == Path('/var/lib/nginx/certs')

    def test_volume_mount_container_path_accessible(self, mocker, mock_compose_file_manager):
        """Test that container paths are accessible through the dirs object."""
        compose_path = Path('/app/docker-compose.yml')

        mock_volume = DockerVolumeMount(
            host='/var/lib/nginx/certs',
            container='/etc/nginx/certs',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        mock_compose_file_manager.get_service_volumes.return_value = [mock_volume]

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # Container should be a Path object
        assert isinstance(storage.dirs.certs.container, Path)
        assert storage.dirs.certs.container == Path('/etc/nginx/certs')

    def test_volume_mount_type_accessible(self, mocker, mock_compose_file_manager):
        """Test that volume type is accessible through the dirs object."""
        compose_path = Path('/app/docker-compose.yml')

        mock_volume = DockerVolumeMount(
            host='/var/lib/nginx/certs',
            container='/etc/nginx/certs',
            type=DockerVolumeType.bind,
            compose_path=compose_path,
        )

        mock_compose_file_manager.get_service_volumes.return_value = [mock_volume]

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        assert storage.dirs.certs.type == DockerVolumeType.bind


class TestProxyStoragePathsIntegration:
    """Integration tests for ProxyStoragePaths with different scenarios."""

    def test_typical_nginx_proxy_configuration(self, mocker, mock_compose_file_manager):
        """Test a typical nginx-proxy setup with common volumes."""
        compose_path = Path('/home/user/fm/sites/example/docker-compose.yml')

        volumes = [
            DockerVolumeMount(
                host='/home/user/fm/sites/example/workspace/nginx-proxy/certs',
                container='/etc/nginx/certs',
                type=DockerVolumeType.bind,
                compose_path=compose_path,
            ),
            DockerVolumeMount(
                host='/home/user/fm/sites/example/workspace/nginx-proxy/ssl',
                container='/etc/letsencrypt',
                type=DockerVolumeType.bind,
                compose_path=compose_path,
            ),
            DockerVolumeMount(
                host='/home/user/fm/sites/example/workspace/nginx-proxy/dhparam',
                container='/etc/nginx/dhparam',
                type=DockerVolumeType.bind,
                compose_path=compose_path,
            ),
        ]

        mock_compose_file_manager.get_service_volumes.return_value = volumes

        storage = ProxyStoragePaths('nginx-proxy', mock_compose_file_manager)

        # All typical nginx volumes should be accessible
        assert hasattr(storage.dirs, 'certs')
        assert hasattr(storage.dirs, 'ssl')
        assert hasattr(storage.dirs, 'dhparam')

        # Verify they point to correct locations
        assert str(storage.dirs.certs.host).endswith('certs')
        assert str(storage.dirs.ssl.host).endswith('ssl')
        assert str(storage.dirs.dhparam.host).endswith('dhparam')
