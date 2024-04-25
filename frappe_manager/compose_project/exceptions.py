from pathlib import Path
from typing import List


class DockerComposeProjectFailedToStartError(Exception):
    def __init__(self, compose_path: Path, services: List[str], message='Failed to start compose services {}.') -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToStopError(Exception):
    def __init__(self, compose_path: Path, services: List[str], message='Failed to stop compose services {}.') -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToRemoveError(Exception):
    def __init__(
        self, compose_path: Path, services: List[str], message='Failed to remove compose services {}.'
    ) -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToPullImagesError(Exception):
    def __init__(
        self, compose_path: Path, services: List[str], message='Failed to pull compose services {} images.'
    ) -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)


class DockerComposeProjectFailedToRestartError(Exception):
    def __init__(
        self, compose_path: Path, services: List[str], message='Failed to pull compose services {} images.'
    ) -> None:
        self.compose_path = compose_path
        self.services = services
        self.message = message.format(self.services)
        super().__init__(self.message)
