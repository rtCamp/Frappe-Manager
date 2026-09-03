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
from typing import Union

from frappe_manager import CLI_SERVICES_DIRECTORY


class DockerVolumeType(str, Enum):
    volume = "volume"
    bind = "bind"


class DockerVolumeMount:
    def __init__(
        self,
        host: str | Path,
        container: str,
        type: str,
        compose_path: Path,
        read_only: bool = False,
    ):
        self.host = host
        self.type = type
        self.container = Path(container)
        self.compose_path = compose_path
        self.read_only = read_only

        if type == DockerVolumeType.bind:
            self.host = Path(self.host)
            # only join ./ paths
            if str(host).startswith("./"):
                self.host = compose_path.parent.joinpath(host)

    def __str__(self):
        source = Path(self.host) if not isinstance(self.host, Path) else self.host

        if self.type == "bind":
            source = str(self.host).replace(str(self.compose_path.parent), ".")

        dest = str(self.container)
        mount = f"{source}:{dest}"
        # A CA bundle (or any bind meant to be read-only) must never be writable by the container
        # that reads it; `:ro` is the only thing that makes that survive a regeneration, since
        # this string is what gets written back to the compose file on disk.
        return f"{mount}:ro" if self.read_only else mount


# For convenient imports: from frappe_manager.docker import ComposeFile, DockerClient, etc.
# These use lazy imports to avoid circular dependency issues
def __getattr__(name):
    """Lazy import to avoid circular dependencies."""
    if name == "ComposeFile":
        from frappe_manager.docker.compose_file import ComposeFile

        return ComposeFile
    if name == "ComposeSecretNotFoundError":
        from frappe_manager.docker.compose_exceptions import ComposeSecretNotFoundError

        return ComposeSecretNotFoundError
    if name == "ComposeServiceNotFound":
        from frappe_manager.docker.compose_exceptions import ComposeServiceNotFound

        return ComposeServiceNotFound
    if name == "DockerClient":
        from frappe_manager.docker.docker_client import DockerClient

        return DockerClient
    if name == "DockerComposeWrapper":
        from frappe_manager.docker.docker_compose import DockerComposeWrapper

        return DockerComposeWrapper
    if name == "DockerException":
        from frappe_manager.docker.docker_exceptions import DockerException

        return DockerException
    if name == "SubprocessOutput":
        from frappe_manager.docker.subprocess_output import SubprocessOutput

        return SubprocessOutput
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    # Compose file management
    "ComposeFile",
    "ComposeSecretNotFoundError",
    "ComposeServiceNotFound",
    # Docker wrappers
    "DockerClient",
    "DockerComposeWrapper",
    "DockerException",
    "SubprocessOutput",
    # Volume utilities
    "DockerVolumeMount",
    "DockerVolumeType",
]

# Progress-bar noise emitted by docker pull/build streams; pass to
# OutputHandler.live_lines(line_filters=...) at docker-streaming call sites.
DOCKER_LINE_NOISE = ("[==", "updating files:")
