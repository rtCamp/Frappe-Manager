from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from frappe_manager import CLI_SERVICES_DIRECTORY
from typing import Union

class DockerVolumeType(str, Enum):
    volume = 'volume'
    bind = 'bind'

class DockerVolumeMount:
    def __init__(self, host: Union[str,Path], container: str, type: str, compose_path: Path):
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

        dest  = str(self.container)
        return f'{source}:{dest}'
