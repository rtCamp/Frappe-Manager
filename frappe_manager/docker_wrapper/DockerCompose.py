from subprocess import Popen, run, TimeoutExpired, CalledProcessError
from pathlib import Path
from typing import Union, Literal, Optional

import shlex

from frappe_manager.utils.docker import (
    parameters_to_options,
    run_command_with_exit_code,
)

# Docker Compose version 2.18.1
class DockerComposeWrapper:
    """
    This class provides one to one mapping between docker compose cli each function.
    Only this args have are different use case.

    Args:
        stream (bool, optional): A boolean flag indicating whether to stream the output of the command as it runs. 
            If set to True, the output will be displayed in real-time. If set to False, the output will be 
            displayed after the command completes. Defaults to False.
        stream_only_exit_code (bool, optional): A boolean flag indicating whether to only stream the exit code of the 
            command. Defaults to False.
    """
    def __init__(self, path: Path, timeout: int = 100):
        # requires valid path directory
        # directory where docker-compose resides
        self.compose_file_path = path.absolute()

        self.docker_compose_cmd = [
            "docker",
            "compose",
            "-f",
            self.compose_file_path.as_posix(),
        ]

    def up(
        self,
        services: list[str] = [],
        detach: bool = True,
        build: bool = False,
        remove_orphans: bool = False,
        no_recreate: bool = False,
        force_recreate: bool = False,
        always_recreate_deps: bool = False,
        quiet_pull: bool = False,
        pull: Literal["missing", "never", "always"] = "missing",
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()

        remove_parameters = ["services","stream", "stream_only_exit_code"]

        up_cmd: list = ["up"]
        up_cmd += services

        up_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        # subprocess_env = dict(os.environ)
        # subprocess_env.update(env)

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + up_cmd, quiet=stream_only_exit_code,
            stream=stream
        )
        return iterator

    def down(
        self,
        timeout: int = 100,
        remove_orphans: bool = False,
        rmi: Union[bool, Literal["all", "local"]] = False,
        volumes: bool = False,
        dry_run: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        down_cmd: list[str] = ["down"]

        remove_parameters = ["stream", "stream_only_exit_code"]

        if not rmi:
            remove_parameters.append("rmi")
        else:
            parameters["rmi"] = "all"

        down_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + down_cmd, quiet=stream_only_exit_code,
            stream=stream
        )
        return iterator

    def start(
        self,
        services: Union[None, list[str]] = None,
        dry_run: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()

        start_cmd: list[str] = ["start"]

        remove_parameters = ["services", "stream", "stream_only_exit_code"]

        start_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        # doesn't checks if service exists or not
        if type(services) == list:
            start_cmd += services

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + start_cmd, quiet=stream_only_exit_code,
            stream=stream
        )
        return iterator

    def restart(
        self,
        services: Union[None, list[str]] = None,
        dry_run: bool = False,
        timeout: int = 100,
        no_deps: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        restart_cmd: list[str] = ["restart"]

        remove_parameters = ["services", "stream", "stream_only_exit_code"]

        restart_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        # doesn't checks if service exists or not
        if type(services) == list:
            restart_cmd += services

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + restart_cmd, quiet=stream_only_exit_code,
            stream=stream
        )
        return iterator

    def stop(
        self,
        services: Union[None, list[str]] = None,
        timeout: int = 100,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        stop_cmd: list[str] = ["stop"]

        remove_parameters = ["services", "stream", "stream_only_exit_code"]

        stop_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(services) == list:
            stop_cmd.extend(services)

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + stop_cmd, quiet=stream_only_exit_code,
            stream=stream
        )
        return iterator

    def exec(
        self,
        service: str,
        command: str,
        detach: bool = False,
        env: Union[None, list[str]] = None,
        no_tty: bool = False,
        privileged: bool = False,
        user: Union[None, str] = None,
        workdir: Union[None, str] = None,
        stream: bool = False,
        stream_only_exit_code: bool = False,
        use_shlex_split: bool = True,
    ):
        parameters: dict = locals()

        exec_cmd: list[str] = ["exec"]

        remove_parameters = [
            "service",
            "stream",
            "stream_only_exit_code",
            "command",
            "env",
            "use_shlex_split",
        ]

        exec_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(env) == list:
            for i in env:
                exec_cmd += ["--env", i]

        exec_cmd += [service]

        if use_shlex_split:
            exec_cmd += shlex.split(command, posix=True)
        else:
            exec_cmd += [command]

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + exec_cmd,
            quiet=stream_only_exit_code,
            stream=stream,
        )
        return iterator

    def ps(
        self,
        service: Union[None, list[str]] = None,
        dry_run: bool = False,
        all: bool = False,
        services: bool = False,
        filter: Union[
            None,
            Literal[
                "paused",
                "restarting",
                "removing",
                "running",
                "dead",
                "created",
                "exited",
            ],
        ] = None,
        format: Union[None, Literal["table", "json"]] = None,
        status: Union[
            None,
            list[
                Literal[
                    "paused",
                    "restarting",
                    "removing",
                    "running",
                    "dead",
                    "created",
                    "exited",
                ]
            ],
        ] = None,
        quiet: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()

        ps_cmd: list[str] = ["ps"]

        remove_parameters = [
            "service",
            "stream",
            "stream_only_exit_code",
            "filter",
            "status",
        ]

        if filter:
            parameters["filter"] = f"status={filter}"

        ps_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(status) == list:
            for i in status:
                ps_cmd += ["--status", i]

        if service:
            ps_cmd += service

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + ps_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator

    def logs(
        self,
        services: Union[None, list[str]] = None,
        dry_run: bool = False,
        follow: bool = False,
        no_color: bool = False,
        no_log_prefix: bool = False,
        since: Union[None, str] = None,
        tail: Union[None, int] = None,
        until: Union[None, int] = None,
        timestamps: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()

        logs_cmd: list[str] = ["logs"]

        remove_parameters = ["services", "stream", "stream_only_exit_code"]

        logs_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if services:
            logs_cmd += services

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + logs_cmd,
            quiet=stream_only_exit_code,
            stream=stream,
        )
        return iterator

    def ls(
        self,
        all: bool = False,
        dry_run: bool = False,
        format: Literal["table", "json"] = "table",
        quiet: bool = False,
    ):
        parameters: dict = locals()

        ls_cmd: list[str] = ["ls"]

        ls_cmd += parameters_to_options(parameters)

        try:
            output = run(self.docker_compose_cmd + ls_cmd, capture_output=True)
            output = output.stdout.decode()
        except:
            return False

        return output

    def pull(
        self,
        dry_run: bool = False,
        ignore_buildable: bool = False,
        ignore_pull_failures: bool = False,
        include_deps: bool = False,
        quiet: bool = False,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()

        pull_cmd: list[str] = ["pull"]

        remove_parameters = ["stream", "stream_only_exit_code"]

        pull_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + pull_cmd,
            quiet=stream_only_exit_code,
            stream=stream,
        )
        return iterator

    def run(
        self,
        service: str,
        command: Optional[str] = None,
        name: Optional[str] = None,
        detach: bool = False,
        rm: bool = False,
        entrypoint: Optional[str] = None,
        use_shlex_split: bool = True,
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        parameters: dict = locals()
        run_cmd: list = ["run"]

        remove_parameters = ["stream", "stream_only_exit_code", "command", "service","use_shlex_split"]

        run_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        run_cmd += [service]

        if command:
            if use_shlex_split:
                run_cmd += shlex.split(command, posix=True)
            else:
                run_cmd += [command]


        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + run_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator

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
            self.docker_compose_cmd + cp_cmd, quiet=stream_only_exit_code, stream=stream
        )
        return iterator
