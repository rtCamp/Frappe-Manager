import json
import shlex
import subprocess
from pathlib import Path
from typing import Literal

from frappe_manager.docker.docker_compose import DockerComposeWrapper, _build_cp_cmd
from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.output_manager.base import OutputHandler
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

    def __init__(self, compose_file_path: Path | None = None, output: OutputHandler | None = None):
        """
        Initializes a DockerClient object.
        Args:
            compose_file_path (Optional[Path]): The path to the Docker Compose file. Defaults to None.
            output (Optional[OutputHandler]): Output handler for docker operations. Defaults to None.
        """
        self.docker_cmd = ["docker"]
        self.output = output
        self.compose: DockerComposeWrapper | None = None
        if compose_file_path:
            self.compose = DockerComposeWrapper(compose_file_path, output=output)

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
            if docker_info["Server"]:
                return True
            return False
        # check if the current user in the docker group and notify the user
        is_current_user_in_group("docker")
        return False

    def cp(
        self,
        source: str,
        destination: str,
        source_container: str | None = None,
        destination_container: str | None = None,
        archive: bool = False,
        follow_link: bool = False,
        stream: bool = False,
    ):
        cp_cmd: list = _build_cp_cmd(source, destination, source_container, destination_container, archive, follow_link)

        iterator = run_command_with_exit_code(self.docker_cmd + cp_cmd, stream=stream)
        return iterator

    def container_names(self, name_prefix: str) -> list[str]:
        """Names of every container, running or not, whose name starts with ``name_prefix``.

        Docker's own record of what exists, which is what makes this usable when the compose file
        that created the containers is gone. ``docker ps`` matches ``--filter name=`` as a substring,
        so the prefix is re-checked here.
        """
        result = subprocess.run(  # noqa: S603
            [*self.docker_cmd, "ps", "-a", "--filter", f"name={name_prefix}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line for line in (n.strip() for n in result.stdout.splitlines()) if line.startswith(name_prefix)]

    def kill(
        self,
        container: str,
        signal: str | None = None,
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
        command: str | None = None,
        env: dict[str, str] | None = None,
        name: str | None = None,
        user: str | None = None,
        volume: list[str] | None = None,
        detach: bool = False,
        entrypoint: str | None = None,
        workdir: str | None = None,
        platform: str | None = None,
        pull: Literal["missing", "never", "always"] = "missing",
        use_shlex_split: bool = True,
        stream: bool = False,
        rm: bool = False,
    ):
        parameters: dict = locals()
        run_cmd: list = ["run"]

        remove_parameters = ["stream", "command", "image", "use_shlex_split", "env"]

        run_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if isinstance(env, dict):
            for i, v in env.items():
                run_cmd += ["--env", f"{i}={v}"]

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
        platform: str | None = None,
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

    def push(
        self,
        image: str,
        stream: bool = True,
    ):
        """Push an image (or repo:tag) to its registry (``docker push <image>``).

        Mirrors :meth:`pull`. Registry authentication is the daemon's own, from
        ``docker login`` or a credential helper; fm never handles it.
        """
        parameters: dict = locals()

        push_cmd: list[str] = ["push"]
        remove_parameters = ["stream", "image"]

        push_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        push_cmd += [image]

        iterator = run_command_with_exit_code(
            self.docker_cmd + push_cmd,
            stream=stream,
        )
        return iterator

    def network_ls(
        self,
        format: str = "{{.Name}}",
        stream: bool = False,
    ) -> list[str]:
        """List Docker networks and return their names."""
        parameters: dict = locals()
        cmd: list[str] = ["network", "ls"]
        remove_parameters = ["stream"]
        cmd += parameters_to_options(parameters, exclude=remove_parameters)
        output: SubprocessOutput = run_command_with_exit_code(self.docker_cmd + cmd, stream=stream)
        names = [line.strip() for line in output.stdout if line.strip()]
        return names

    def network_inspect(
        self,
        network_name: str,
        format: str = "{{json .IPAM.Config}}",
        stream: bool = False,
    ) -> list:
        """Inspect a Docker network and return parsed JSON output."""
        parameters: dict = locals()
        cmd: list[str] = ["network", "inspect", network_name]
        remove_parameters = ["stream", "network_name"]
        cmd += parameters_to_options(parameters, exclude=remove_parameters)
        try:
            output: SubprocessOutput = run_command_with_exit_code(self.docker_cmd + cmd, stream=stream)
            if output.stdout:
                return json.loads(" ".join(output.stdout))
        except DockerException:
            return []
        return []

    def container_inspect(
        self,
        container_name: str,
        format: str = "{{json .NetworkSettings.Networks}}",
        stream: bool = False,
    ) -> dict:
        """Inspect a container and return parsed JSON of its network settings."""
        parameters: dict = locals()
        cmd: list[str] = ["inspect", container_name]
        remove_parameters = ["stream", "container_name"]
        cmd += parameters_to_options(parameters, exclude=remove_parameters)
        try:
            output: SubprocessOutput = run_command_with_exit_code(self.docker_cmd + cmd, stream=stream)
            if output.stdout:
                data = json.loads(" ".join(output.stdout))
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
                return data
        except DockerException:
            return {}
        return {}

    def image_labels(self, image: str) -> dict:
        """Return the image's config labels as a dict (``{}`` if unavailable)."""
        cmd: list[str] = ["inspect", image, "--format", "{{json .Config.Labels}}"]
        try:
            output: SubprocessOutput = run_command_with_exit_code(self.docker_cmd + cmd, stream=False)
            if output.stdout:
                data = json.loads(" ".join(output.stdout))
                return data or {}
        except DockerException:
            return {}
        return {}

    def images(
        self,
        format: Literal["json"] = "json",
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

    def tag(
        self,
        source_image: str,
        target_image: str,
        stream: bool = False,
    ):
        """
        Tag a source image with a new target name/tag.

        Args:
            source_image: Source image (e.g., "alpine:3.18" or image ID)
            target_image: Target image tag (e.g., "myrepo/alpine:latest")
            stream: Whether to stream output (default: False)

        Returns:
            SubprocessOutput or iterator if streaming

        Example:
            docker.tag("alpine:3.18", "myrepo/alpine:prod")
        """
        parameters: dict = locals()

        tag_cmd: list[str] = ["tag"]
        remove_parameters = ["stream", "source_image", "target_image"]

        tag_cmd += parameters_to_options(parameters, exclude=remove_parameters)
        tag_cmd += [source_image, target_image]

        iterator = run_command_with_exit_code(
            self.docker_cmd + tag_cmd,
            stream=stream,
        )
        return iterator

    def rmi(
        self,
        image: str | list[str],
        force: bool = False,
        no_prune: bool = False,
        stream: bool = False,
    ):
        """
        Remove one or more images.

        Args:
            image: Image name/ID or list of images to remove
            force: Force removal of the image (default: False)
            no_prune: Do not delete untagged parents (default: False)
            stream: Whether to stream output (default: False)

        Returns:
            SubprocessOutput or iterator if streaming

        Example:
            docker.rmi("myrepo/alpine:old")
            docker.rmi(["img1", "img2"], force=True)
        """
        parameters: dict = locals()

        rmi_cmd: list[str] = ["rmi"]
        remove_parameters = ["stream", "image"]

        rmi_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if isinstance(image, str):
            rmi_cmd += [image]
        else:
            rmi_cmd += image

        iterator = run_command_with_exit_code(
            self.docker_cmd + rmi_cmd,
            stream=stream,
        )
        return iterator

    def create_temp_container(self, image: str, name: str | None = None, **run_kwargs) -> "TempContainer":
        """
        Create a temporary container (context manager).

        Args:
            image: Image to use
            name: Container name (random if not provided)
            **run_kwargs: Additional run arguments

        Returns:
            TempContainer context manager

        Example:
            with docker.create_temp_container("alpine") as container:
                docker.cp(source="/etc/hosts", destination="./",
                         source_container=container.name)
        """
        if name is None:
            from frappe_manager.utils.docker import generate_random_text

            name = f"temp_{generate_random_text(10)}"

        return TempContainer(self, image, name, run_kwargs)

    def is_running(self) -> bool:
        """Alias for server_running() for better readability"""
        return self.server_running()

    def __enter__(self) -> "DockerClient":
        """
        Enter context manager - enables automatic cleanup of compose environment.

        Returns:
            Self for use in 'with' statement

        Example:
            with DockerClient(compose_path) as docker:
                docker.compose.up(detach=True)
                # Work with docker...
                # Compose environment auto-cleaned up on exit

        Note:
            If a compose file path was provided during initialization, the compose
            environment will be automatically cleaned up when the context exits.
        """
        # If compose is available, enter its context
        if self.compose:
            self.compose.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """
        Exit context manager - cleans up compose environment if present.

        Args:
            exc_type: Exception type (if any)
            exc_val: Exception value (if any)
            exc_tb: Exception traceback (if any)

        Returns:
            False (does not suppress exceptions)

        Note:
            If a compose file path was provided during initialization, this will
            delegate to the compose wrapper's cleanup logic. Any cleanup errors
            are silently ignored (best-effort cleanup).
        """
        # If compose is available, exit its context
        if self.compose:
            self.compose.__exit__(exc_type, exc_val, exc_tb)

        # Don't suppress exceptions - return False
        return False


class TempContainer:
    """Context manager for temporary Docker containers"""

    def __init__(self, docker: DockerClient, image: str, name: str, run_kwargs: dict):
        self.docker = docker
        self.image = image
        self.name = name
        self.run_kwargs = run_kwargs
        self._started = False

    def __enter__(self) -> "TempContainer":
        """Start the temporary container"""
        from frappe_manager.docker import DockerException

        try:
            self.docker.run(
                image=self.image,
                name=self.name,
                detach=True,
                entrypoint="bash",
                command="tail -f /dev/null",
                stream=False,
                **self.run_kwargs,
            )
            self._started = True
        except DockerException as e:
            raise RuntimeError(f"Failed to create temp container: {e}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Remove the temporary container"""
        from frappe_manager.docker import DockerException

        if self._started:
            try:
                self.docker.rm(container=self.name, force=True, stream=False)
            except DockerException:
                pass  # Best effort cleanup
        return False
