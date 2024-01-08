from typing import Literal, Optional
import json
from frappe_manager.docker_wrapper.DockerCompose import DockerComposeWrapper
from pathlib import Path
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.utils import (
    is_current_user_in_group,
    parameters_to_options,
    run_command_with_exit_code,
)


class DockerClient:
    def __init__(self, compose_file_path: Optional[Path] = None):
        self.docker_cmd = ["docker"]
        if compose_file_path:
            self.compose = DockerComposeWrapper(compose_file_path)

    def version(self) -> dict:
        """
        The `version` function retrieves the version information of a Docker container and returns it as a
        JSON object.
        :return: a dictionary object named "output".
        """
        parameters: dict = locals()

        parameters["format"] = "json"

        ver_cmd: list = ["version"]

        ver_cmd += parameters_to_options(parameters)

        iterator = run_command_with_exit_code(self.docker_cmd + ver_cmd, quiet=False)

        output: dict = {}
        try:
            for source, line in iterator:
                if source == "stdout":
                    output = json.loads(line.decode())
        except Exception as e:
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
        if "Server" in docker_info:
            return True
        else:
            # check if the current user in the docker group and notify the user
            is_current_user_in_group("docker")

            return False

    def cp(
        self,
        source: str,
        destination: str,
        source_container: str = None,
        destination_container: str = None,
        archive: bool = False,
        follow_link: bool = False,
        quiet: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        cp_cmd: list = ["cp"]

        remove_parameters = [
            "stream",
            "stream_only_exit_code",
            "source",
            "destination",
            "source_container",
            "destination_container",
        ]

        cp_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if source_container:
            source = f"{source_container}:{source}"

        if destination_container:
            destination = f"{destination_container}:{destination}"

        cp_cmd += [f"{source}"]
        cp_cmd += [f"{destination}"]

        iterator = run_command_with_exit_code(
            self.docker_cmd + cp_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator

    def kill(
        self,
        container: str,
        signal: Optional[str] = None,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        kill_cmd: list = ["kill"]

        remove_parameters = ["stream", "stream_only_exit_code", "container"]

        kill_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        kill_cmd += [f"{container}"]

        iterator = run_command_with_exit_code(
            self.docker_cmd + kill_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator

    def rm(
        self,
        container: str,
        force: bool = False,
        link: bool = False,
        volumes: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        rm_cmd: list = ["rm"]

        remove_parameters = ["stream", "stream_only_exit_code", "container"]

        rm_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        rm_cmd += [f"{container}"]

        iterator = run_command_with_exit_code(
            self.docker_cmd + rm_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator

    def run(
        self,
        command: str,
        image: str,
        name: Optional[str] = None,
        detach: bool = False,
        entrypoint: Optional[str] = None,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        run_cmd: list = ["run"]

        remove_parameters = ["stream", "stream_only_exit_code", "image", "command"]

        run_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        run_cmd += [f"{image}"]

        if command:
            run_cmd += [f"{command}"]

        iterator = run_command_with_exit_code(
            self.docker_cmd + run_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator
