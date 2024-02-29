import json
import shlex

from typing import Literal, Optional
from pathlib import Path
from frappe_manager.docker_wrapper.DockerCompose import DockerComposeWrapper
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.docker import (
    is_current_user_in_group,
    parameters_to_options,
    run_command_with_exit_code,
)


class DockerClient:
    """
    This class provide one to one mapping to the docker command.

    Only this args have are different use case.
        stream (bool, optional): A boolean flag indicating whether to stream the output of the command as it runs. 
            If set to True, the output will be displayed in real-time. If set to False, the output will be 
            displayed after the command completes. Defaults to False.
        stream_only_exit_code (bool, optional): A boolean flag indicating whether to only stream the exit code of the 
            command. Defaults to False.
    """

    def __init__(self, compose_file_path: Optional[Path] = None):
        """
        Initializes a DockerClient object.
        Args:
            compose_file_path (Optional[Path]): The path to the Docker Compose file. Defaults to None.
        """
        self.docker_cmd = ["docker"]
        if compose_file_path:
            self.compose = DockerComposeWrapper(compose_file_path)

    def version(self) -> dict:
        """
        Retrieves the version information of the Docker client.

        Returns:
            A dictionary containing the version information.
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
        Checks if the Docker server is running.

        Returns:
            bool: True if the Docker server is running, False otherwise.
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
        image: str,
        command: Optional[str] = None,
        name: Optional[str] = None,
        volume: Optional[str] = None,
        detach: bool = False,
        entrypoint: Optional[str] = None,
        pull: Literal["missing", "never", "always"] = "missing",
        use_shlex_split: bool = True,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        run_cmd: list = ["run"]

        remove_parameters = ["stream", "stream_only_exit_code", "command", "image","use_shlex_split"]

        run_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        run_cmd += [f"{image}"]

        if command:
            if use_shlex_split:
                run_cmd += shlex.split(command, posix=True)
            else:
                run_cmd += [command]

        iterator = run_command_with_exit_code(
            self.docker_cmd + run_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator

    def pull(
        self,
        container_name: str,
        all_tags: bool = False,
        platform: Optional[str] = None,
        quiet: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()

        pull_cmd: list[str] = ["pull"]

        remove_parameters = ["stream", "stream_only_exit_code","container_name"]

        pull_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        pull_cmd += [container_name]

        iterator = run_command_with_exit_code(
            self.docker_cmd + pull_cmd,
            quiet=stream_only_exit_code,
            stream=stream,
        )
        return iterator
