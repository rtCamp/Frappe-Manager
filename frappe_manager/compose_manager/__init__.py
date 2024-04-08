from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from frappe_manager import CLI_SERVICES_DIRECTORY
from typing import Union

class DockerVolumeType(str, Enum):
    volume = 'volume'
    bind = 'bind'

class DockerVolumeMount:
    def __init__(self, host: Union[str,Path], container: str, type: str):
        self.host = host
        self.type = type
        self.container = Path(container)

        if type == DockerVolumeType.bind:

            self.host = Path(self.host)
            # only join ./ paths
            if str(host).startswith('./'):
               self.host = CLI_SERVICES_DIRECTORY.joinpath(host)
