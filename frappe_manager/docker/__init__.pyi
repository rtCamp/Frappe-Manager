"""Type stubs for frappe_manager.docker module."""

from enum import Enum
from pathlib import Path

# Volume types
class DockerVolumeType(str, Enum):
    volume: str
    bind: str

class DockerVolumeMount:
    host: str | Path
    type: str
    container: Path
    compose_path: Path
    def __init__(self, host: str | Path, container: str, type: str, compose_path: Path) -> None: ...

# Import actual classes for type checking
from frappe_manager.docker.compose_exceptions import (
    ComposeSecretNotFoundError as ComposeSecretNotFoundError,
)
from frappe_manager.docker.compose_exceptions import (
    ComposeServiceNotFound as ComposeServiceNotFound,
)
from frappe_manager.docker.compose_file import ComposeFile as ComposeFile
from frappe_manager.docker.docker_client import DockerClient as DockerClient
from frappe_manager.docker.docker_compose import DockerComposeWrapper as DockerComposeWrapper
from frappe_manager.docker.docker_exceptions import DockerException as DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput as SubprocessOutput

__all__ = [
    # Volume types
    "DockerVolumeType",
    "DockerVolumeMount",
    # Compose file management
    "ComposeFile",
    "ComposeSecretNotFoundError",
    "ComposeServiceNotFound",
    # Docker wrappers
    "DockerClient",
    "DockerComposeWrapper",
    "DockerException",
    "SubprocessOutput",
]
