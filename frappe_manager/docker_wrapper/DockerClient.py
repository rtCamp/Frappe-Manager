import json
import shlex
from sys import exception

from typing import Literal, Optional, List
from pathlib import Path
from frappe_manager.docker_wrapper.DockerCompose import DockerComposeWrapper
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.docker_wrapper.DockerException import DockerException
from frappe_manager.utils.docker import (
    SubprocessOutput,
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

        try:
            output: SubprocessOutput = run_command_with_exit_code(self.docker_cmd + ver_cmd, stream=False)
            version: dict = json.loads(" ".join(output.stdout))
            return version
        except DockerException as e:
            version: dict = json.loads(" ".join(e.output.stdout))
            return version

    def server_running(self) -> bool:
        """
        Checks if the Docker server is running.

        Returns:
            bool: True if the Docker server is running, False otherwise.
        """
        docker_info = self.version()

        if "Server" in docker_info:
            if docker_info['Server']:
                return True
            else:
                return False
        else:
            # check if the current user in the docker group and notify the user
            is_current_user_in_group("docker")
            return False

    def cp(
        self,
        source: str,
        destination: str,
        source_container: Optional[str] = None,
        destination_container: Optional[str] = None,
        archive: bool = False,
        follow_link: bool = False,
        stream: bool = False,
    ):
        parameters: dict = locals()
        cp_cmd: list = ["cp"]

        remove_parameters = [
            "stream",
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

        iterator = run_command_with_exit_code(self.docker_cmd + cp_cmd, stream=stream)
        return iterator

    def kill(
        self,
        container: str,
        signal: Optional[str] = None,
        stream: bool = False,
    ):
        parameters: dict = locals()
        kill_cmd: list = ["kill"]

        remove_parameters = ["stream", "container"]

        kill_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        kill_cmd += [f"{container}"]

        iterator = run_command_with_exit_code(self.docker_cmd + kill_cmd, stream=stream)
        return iterator

    def rm(
        self,
        container: str,
        force: bool = False,
        link: bool = False,
        volumes: bool = False,
        stream: bool = False,
    ):
        parameters: dict = locals()
        rm_cmd: list = ["rm"]

        remove_parameters = ["stream", "container"]

        rm_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        rm_cmd += [f"{container}"]

        iterator = run_command_with_exit_code(self.docker_cmd + rm_cmd, stream=stream)
        return iterator

    def run(
        self,
        image: str,
        command: Optional[str] = None,
        env: Optional[List[str]] = None,
        name: Optional[str] = None,
        volume: Optional[str] = None,
        detach: bool = False,
        entrypoint: Optional[str] = None,
        pull: Literal["missing", "never", "always"] = "missing",
        use_shlex_split: bool = True,
        stream: bool = False,
    ):
        parameters: dict = locals()
        run_cmd: list = ["run"]

        remove_parameters = ["stream", "command", "image", "use_shlex_split", "env"]

        run_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if isinstance(env, list):
            for i in env:
                run_cmd += ["--env", i]

        run_cmd += [f"{image}"]

        if command:
            if use_shlex_split:
                run_cmd += shlex.split(command, posix=True)
            else:
                run_cmd += [command]

        iterator = run_command_with_exit_code(self.docker_cmd + run_cmd, stream=stream)
        return iterator

    def pull(
        self,
        container_name: str,
        all_tags: bool = False,
        platform: Optional[str] = None,
        stream: bool = False,
    ):
        parameters: dict = locals()

        pull_cmd: list[str] = ["pull"]

        remove_parameters = ["stream", "container_name"]

        pull_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        pull_cmd += [container_name]

        iterator = run_command_with_exit_code(
            self.docker_cmd + pull_cmd,
            stream=stream,
        )
        return iterator

    def images(
        self,
        format: Literal['json'] = 'json',
    ):
        parameters: dict = locals()

        images_cmd: list[str] = ["images"]
        remove_parameters = []

        images_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        output: SubprocessOutput = run_command_with_exit_code(
            self.docker_cmd + images_cmd,
            stream=False,
        )

        images = []

        if output.stdout:
            for image in output.stdout:
                images.append(json.loads(image))

        return images
