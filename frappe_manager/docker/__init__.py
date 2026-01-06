"""
Docker module for Frappe Manager.

This module provides unified Docker functionality including:
- Docker Compose file editing and building (ComposeFile)
- Docker client wrapper (DockerClient)
- Docker Compose CLI wrapper (DockerComposeWrapper)
- Volume mount utilities (DockerVolumeMount, DockerVolumeType)
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from frappe_manager import CLI_SERVICES_DIRECTORY
from typing import Union


class DockerVolumeType(str, Enum):
    volume = 'volume'
    bind = 'bind'


class DockerVolumeMount:
    def __init__(self, host: Union[str, Path], container: str, type: str, compose_path: Path):
        self.host = host
        self.type = type
        self.container = Path(container)
        self.compose_path = compose_path

        if type == DockerVolumeType.bind:
            self.host = Path(self.host)
            # only join ./ paths
            if str(host).startswith('./'):
                self.host = compose_path.parent.joinpath(host)

    def __str__(self):
        source = Path(self.host) if not isinstance(self.host, Path) else self.host

        if self.type == 'bind':
            source = str(self.host).replace(str(self.compose_path.parent), '.')

        dest = str(self.container)
        return f'{source}:{dest}'


# For convenient imports: from frappe_manager.docker import ComposeFile, DockerClient, etc.
# These use lazy imports to avoid circular dependency issues
def __getattr__(name):
    """Lazy import to avoid circular dependencies."""
    if name == 'ComposeFile':
        from frappe_manager.docker.compose_file import ComposeFile
        return ComposeFile
    elif name == 'ComposeSecretNotFoundError':
        from frappe_manager.docker.compose_exceptions import ComposeSecretNotFoundError
        return ComposeSecretNotFoundError
    elif name == 'ComposeServiceNotFound':
        from frappe_manager.docker.compose_exceptions import ComposeServiceNotFound
        return ComposeServiceNotFound
    elif name == 'DockerClient':
        from frappe_manager.docker.docker_client import DockerClient
        return DockerClient
    elif name == 'DockerComposeWrapper':
        from frappe_manager.docker.docker_compose import DockerComposeWrapper
        return DockerComposeWrapper
    elif name == 'DockerException':
        from frappe_manager.docker.docker_exceptions import DockerException
        return DockerException
    elif name == 'SubprocessOutput':
        from frappe_manager.docker.subprocess_output import SubprocessOutput
        return SubprocessOutput
    elif name == 'ComposeOrchestrator':
        from frappe_manager.docker.compose_orchestrator import ComposeOrchestrator
        return ComposeOrchestrator
    elif name == 'ComposeOrchestratorException':
        from frappe_manager.docker.compose_orchestrator_exceptions import ComposeOrchestratorException
        return ComposeOrchestratorException
    elif name == 'DeploymentFailedError':
        from frappe_manager.docker.compose_orchestrator_exceptions import DeploymentFailedError
        return DeploymentFailedError
    elif name == 'TeardownFailedError':
        from frappe_manager.docker.compose_orchestrator_exceptions import TeardownFailedError
        return TeardownFailedError
    elif name == 'HealthCheckTimeoutError':
        from frappe_manager.docker.compose_orchestrator_exceptions import HealthCheckTimeoutError
        return HealthCheckTimeoutError
    elif name == 'RestartFailedError':
        from frappe_manager.docker.compose_orchestrator_exceptions import RestartFailedError
        return RestartFailedError
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    # Compose file management
    'ComposeFile',
    'ComposeSecretNotFoundError',
    'ComposeServiceNotFound',
    
    # Docker wrappers
    'DockerClient',
    'DockerComposeWrapper',
    'DockerException',
    'SubprocessOutput',
    
    # Orchestrator
    'ComposeOrchestrator',
    'ComposeOrchestratorException',
    'DeploymentFailedError',
    'TeardownFailedError',
    'HealthCheckTimeoutError',
    'RestartFailedError',
    
    # Volume utilities
    'DockerVolumeMount',
    'DockerVolumeType',
]
