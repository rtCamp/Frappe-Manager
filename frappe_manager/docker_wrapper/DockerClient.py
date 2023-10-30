from typing import Literal, Optional
import json
from frappe_manager.docker_wrapper.DockerCompose import DockerComposeWrapper
from pathlib import Path
from frappe_manager.docker_wrapper.utils import (
    parameters_to_options,
    run_command_with_exit_code,
)

class DockerClient:
    def __init__(self, compose_file_path: Optional[Path] = None):
        self.docker_cmd= ['docker']
        if compose_file_path:
            self.compose = DockerComposeWrapper(compose_file_path)

    def version(self) -> dict:
        """
        The `version` function retrieves the version information of a Docker container and returns it as a
        JSON object.
        :return: a dictionary object named "output".
        """
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
            return {}
        return output

    def server_running(self) -> bool:
        """
        The function `server_running` checks if the Docker server is running and returns a boolean value
        indicating its status.
        :return: a boolean value. If the 'Server' key in the 'docker_info' dictionary is truthy, then the
        function returns True. Otherwise, it returns False.
        """
        docker_info = self.version()
        if 'Server' in docker_info:
            return True
        else:
            return False
