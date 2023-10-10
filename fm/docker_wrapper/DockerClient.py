from typing import Literal, Optional
import json

from fm.docker_wrapper.DockerCompose import DockerComposeWrapper
from pathlib import Path
from fm.docker_wrapper.utils import (
    parameters_to_options,
    run_command_with_exit_code,
)

class DockerClient:
    def __init__(self, compose_file_path: Optional[Path] = None):
        self.docker_cmd= ['docker']
        if compose_file_path:
            self.compose = DockerComposeWrapper(compose_file_path)

    def version(
            self,
    ):
        parameters: dict = locals()

        parameters['format'] = 'json'

        ver_cmd: list = ["version"]

        ver_cmd += parameters_to_options(parameters)

        iterator = run_command_with_exit_code(
            self.docker_cmd + ver_cmd, quiet=False
        )

        output: dict = {}
        try:
            for source ,line in iterator:
                if source == 'stdout':
                    output = json.loads(line.decode())
        except Exception:
            pass

        return output

    def server_running(self) -> bool:
        docker_info = self.version()
        if docker_info['Server']:
            return True
        return False
