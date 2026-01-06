"""
Provides access to nginx-proxy volume mount paths.

This module separates the concern of reading Docker volume configurations
from controlling nginx operations, improving testability and separation of concerns.
"""

from pathlib import Path
from typing import List
from frappe_manager.docker import DockerVolumeMount, DockerVolumeType, ComposeFile
from frappe_manager.utils.helpers import create_class_from_dict


class ProxyStoragePaths:
    """
    Provides access to nginx-proxy volume mount paths.
    
    This class is responsible only for reading and exposing the Docker volume
    mount configuration for the nginx-proxy service. It does not perform any
    operations on nginx itself.
    
    Attributes:
        service_name: Name of the nginx service in docker-compose
        compose_file_manager: The compose file manager for reading volumes
        dirs: Dynamic attribute containing volume mount information
    """
    
    def __init__(
        self,
        service_name: str,
        compose_file_manager: ComposeFile,
    ):
        """
        Initialize the proxy storage paths reader.
        
        Args:
            service_name: Name of the nginx service (e.g., 'nginx', 'nginx-proxy')
            compose_file_manager: The compose file manager for reading volumes
        """
        self.service_name = service_name
        self.compose_file_manager = compose_file_manager
        self.dirs = self._get_docker_volume_dirs()
    
    def _get_docker_volume_dirs(self):
        """
        Read volume mount paths from the compose file.
        
        Returns:
            A dynamic class instance with attributes for each volume mount.
            Each attribute is a DockerVolumeMount object providing both host
            and container paths.
        """
        from pathlib import Path
        
        all_volumes: List[DockerVolumeMount] = self.compose_file_manager.get_service_volumes(
            self.service_name
        )
        dirs = {}
        for volume in all_volumes:
            if volume.type == DockerVolumeType.bind:
                # Ensure host is a Path object to access .name property
                host_path = Path(volume.host) if not isinstance(volume.host, Path) else volume.host
                name = str(host_path.name)
                dirs[name] = volume
        dirs_class = create_class_from_dict('dirs', dirs)
        return dirs_class()
