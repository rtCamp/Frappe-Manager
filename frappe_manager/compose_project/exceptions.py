from pathlib import Path
from typing import List


class DockerComposeProjectFailedToStartError(Exception):
    """Exception raised when Docker Compose project fails to start."""

    def __init__(self, compose_path: Path, services: List[str], message='Failed to start compose services {}.') -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToStopError(Exception):
    """Exception raised when Docker Compose project fails to stop."""

    def __init__(self, compose_path: Path, services: List[str], message='Failed to stop compose services {}.') -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToRemoveError(Exception):
    """Exception raised when Docker Compose project fails to remove services."""

    def __init__(
        self, compose_path: Path, services: List[str], message='Failed to remove compose services {}.'
    ) -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToPullImagesError(Exception):
    """Exception raised when Docker Compose project fails to pull images."""

    def __init__(
        self, compose_path: Path, services: List[str], message='Failed to pull compose services {} images.'
    ) -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToRestartError(Exception):
    """Exception raised when Docker Compose project fails to restart services."""

    def __init__(
        self, compose_path: Path, services: List[str], message='Failed to restart compose services {} images.'
    ) -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)
