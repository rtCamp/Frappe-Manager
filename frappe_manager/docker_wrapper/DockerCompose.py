from subprocess import Popen, run, TimeoutExpired, CalledProcessError
from pathlib import Path
from typing import Union, Literal

import shlex
from frappe_manager.docker_wrapper.utils import (
    parameters_to_options,
    run_command_with_exit_code,
)

# Docker Compose version 2.18.1
class DockerComposeWrapper:
    """
    This class provides one to one mapping between docker compose cli each function.

    There are two parameter which are different:
    :param stream: A boolean flag indicating whether to stream the output of the command as it runs. If
    set to True, the output will be displayed in real-time. If set to False, the output will be
    displayed after the command completes, defaults to False
    :type stream: bool (optional)
    :param stream_only_exit_code: A boolean flag indicating whether to only stream the exit code of the
    command, defaults to False
    :type stream_only_exit_code: bool (optional)
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
        detach: bool = True,
        build: bool = False,
        remove_orphans: bool = False,
        no_recreate: bool = False,
        always_recreate_deps: bool = False,
        services: list[str] = [],
        quiet_pull: bool = False,
        pull: Literal["missing", "never", "always"] = "missing",
        stream: bool = False,
        stream_only_exit_code: bool = False,
    ):
        """
        The `up` function is a Python method that runs the `docker-compose up` command with various options
        and returns an iterator.
        
        :param detach: A boolean flag indicating whether to run containers in the background or not. If set
        to True, containers will be detached and run in the background. If set to False, containers will run
        in the foreground, defaults to True
        :type detach: bool (optional)
        :param build: A boolean flag indicating whether to build images before starting containers, defaults
        to False
        :type build: bool (optional)
        :param remove_orphans: A boolean flag indicating whether to remove containers for services that are
        no longer defined in the Compose file, defaults to False
        :type remove_orphans: bool (optional)
        :param no_recreate: A boolean flag indicating whether to recreate containers that already exist,
        defaults to False
        :type no_recreate: bool (optional)
        :param always_recreate_deps: A boolean flag indicating whether to always recreate dependencies,
        defaults to False
        :type always_recreate_deps: bool (optional)
        :param services: A list of services to be started. These services are defined in the Docker Compose
        file and represent different components of your application
        :type services: list[str]
        :param quiet_pull: A boolean flag indicating whether to suppress the output of the pull command
        during the "up" operation, defaults to False
        :type quiet_pull: bool (optional)
        :param pull: The `pull` parameter determines when to pull new images. It can have one of three
        values: "missing", "never", or "always", defaults to missing
        :type pull: Literal["missing", "never", "always"] (optional)
        :param stream: A boolean flag indicating whether to stream the output of the command as it runs,
        defaults to False
        :type stream: bool (optional)
        :param stream_only_exit_code: A boolean flag indicating whether to only stream the exit code of the
        command, defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: an iterator.
        """
        parameters: dict = locals()

        remove_parameters = ["stream", "stream_only_exit_code"]

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

    # @handle_docker_error
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
        """
        The `start` function is used to start Docker services specified in the `services` parameter, with
        options for dry run, streaming output, and checking only the exit code.
        
        :param services: A list of services to start. If None, all services will be started
        :type services: Union[None, list[str]]
        :param dry_run: A boolean flag indicating whether the start operation should be performed in dry run
        mode or not. If set to True, the start operation will not actually be executed, but the command and
        options will be printed. If set to False, the start operation will be executed, defaults to False
        :type dry_run: bool (optional)
        :param stream: A boolean flag indicating whether to stream the output of the command in real-time or
        not. If set to True, the output will be streamed as it is generated. If set to False, the output
        will be returned as a single string after the command completes, defaults to False
        :type stream: bool (optional)
        :param stream_only_exit_code: A boolean flag indicating whether only the exit code should be
        streamed, defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: The `start` method returns an iterator.
        """
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
        """
        The `restart` function restarts specified services in a Docker Compose environment with various
        options and returns an iterator.
        
        :param services: A list of services to restart. If set to None, all services will be restarted
        :type services: Union[None, list[str]]
        :param dry_run: A boolean flag indicating whether the restart operation should be performed as a dry
        run (without actually restarting the services), defaults to False
        :type dry_run: bool (optional)
        :param timeout: The `timeout` parameter specifies the maximum time (in seconds) to wait for the
        services to restart before timing out, defaults to 100
        :type timeout: int (optional)
        :param no_deps: A boolean flag indicating whether to restart the services without recreating their
        dependent services, defaults to False
        :type no_deps: bool (optional)
        :param stream: A boolean flag indicating whether to stream the output of the restart command or not.
        If set to True, the output will be streamed in real-time. If set to False, the output will be
        returned as an iterator, defaults to False
        :type stream: bool (optional)
        :param stream_only_exit_code: A boolean flag indicating whether to only stream the exit code of the
        restart command, defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: The `restart` method returns an iterator.
        """
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        restart_cmd: list[str] = ["restart"]

        remove_parameters = ["service", "stream", "stream_only_exit_code"]

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
        """
        The `stop` function stops specified services in a Docker Compose environment, with options for
        timeout, streaming output, and checking for service existence.
        
        :param services: A list of service names to stop. If None, all services will be stopped
        :type services: Union[None, list[str]]
        :param timeout: The `timeout` parameter specifies the maximum time (in seconds) to wait for the
        services to stop before forcefully terminating them, defaults to 100
        :type timeout: int (optional)
        :param stream: A boolean flag indicating whether to stream the output of the command as it is
        executed, defaults to False
        :type stream: bool (optional)
        :param stream_only_exit_code: The `stream_only_exit_code` parameter is a boolean flag that
        determines whether only the exit code of the command should be streamed or not. If set to `True`,
        only the exit code will be streamed, otherwise, the full output of the command will be streamed,
        defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: The `stop` method returns an iterator.
        """
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        stop_cmd: list[str] = ["stop"]

        remove_parameters = ["services", "stream", "stream_only_exit_code"]

        stop_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        # doesn't checks if service exists or not
        if type(services) == list:
            stop_cmd += services

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
    ):
        """
        The `exec` function in Python executes a command in a Docker container and returns an iterator for
        the command's output.
        
        :param service: The `service` parameter is a string that represents the name of the service you want
        to execute the command on
        :type service: str
        :param command: The `command` parameter is a string that represents the command to be executed
        within the specified service
        :type command: str
        :param detach: A boolean flag indicating whether the command should be detached from the terminal,
        defaults to False
        :type detach: bool (optional)
        :param env: The `env` parameter is a list of environment variables that you can pass to the command
        being executed. Each element in the list should be a string in the format "KEY=VALUE". These
        environment variables will be set for the duration of the command execution
        :type env: Union[None, list[str]]
        :param no_tty: A boolean flag indicating whether to allocate a pseudo-TTY for the executed command,
        defaults to False
        :type no_tty: bool (optional)
        :param privileged: A boolean flag indicating whether the command should be executed with elevated
        privileges, defaults to False
        :type privileged: bool (optional)
        :param user: The `user` parameter is used to specify the username or UID (User Identifier) to run
        the command as within the container. If `user` is set to `None`, the command will be executed as the
        default user in the container
        :type user: Union[None, str]
        :param workdir: The `workdir` parameter specifies the working directory for the command to be
        executed within the container. If `workdir` is set to `None`, the command will be executed in the
        default working directory of the container
        :type workdir: Union[None, str]
        :param stream: A boolean flag indicating whether to stream the output of the command execution,
        defaults to False
        :type stream: bool (optional)
        :param stream_only_exit_code: The `stream_only_exit_code` parameter is a boolean flag that
        determines whether only the exit code of the command should be streamed or the entire output. If
        `stream_only_exit_code` is set to `True`, only the exit code will be streamed. If it is set to
        `False`, the, defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: an iterator.
        """
        parameters: dict = locals()

        exec_cmd: list[str] = ["exec"]

        remove_parameters = [
            "service",
            "stream",
            "stream_only_exit_code",
            "command",
            "env",
        ]

        exec_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(env) == list:
            for i in env:
                exec_cmd += ["--env", i]

        exec_cmd += [service]

        exec_cmd += shlex.split(command)

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
        """
        The `ps` function is a Python method that executes the `docker-compose ps` command with various
        parameters and returns an iterator.
        
        :param service: A list of service names to filter the results by. If None, all services are included
        :type service: Union[None, list[str]]
        :param dry_run: A boolean flag indicating whether the command should be executed in dry run mode,
        where no changes are actually made, defaults to False
        :type dry_run: bool (optional)
        :param all: A boolean flag indicating whether to show all containers, including stopped ones,
        defaults to False
        :type all: bool (optional)
        :param services: A boolean flag indicating whether to display only service names or all container
        information, defaults to False
        :type services: bool (optional)
        :param filter: The `filter` parameter is used to filter the list of containers based on their
        status. It accepts the following values:
        :type filter: Union[j ,, 
        :param format: The `format` parameter specifies the output format for the `ps` command. It can be
        set to either "table" or "json"
        :type format: Union[None, Literal["table", "json"]]
        :param status: The `status` parameter is used to filter the output of the `ps` command based on the
        status of the services. It can be a list of status values such as "paused", "restarting",
        "removing", "running", "dead", "created", or "exited"
        :type status: Union[
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
                ]
        :param quiet: A boolean flag indicating whether to suppress output and only display essential
        information, defaults to False
        :type quiet: bool (optional)
        :param stream: A boolean flag indicating whether to stream the output of the command in real-time,
        defaults to False
        :type stream: bool (optional)
        :param stream_only_exit_code: A boolean flag indicating whether to only stream the exit code of the
        command, defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: The `ps` method returns an iterator.
        """
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
        """
        The `logs` function in Python takes in various parameters and returns an iterator that runs a Docker
        Compose command to retrieve logs from specified services.
        
        :param services: A list of services for which to retrieve logs. If None, logs for all services will
        be retrieved
        :type services: Union[None, list[str]]
        :param dry_run: A boolean flag indicating whether the command should be executed in dry run mode,
        where no changes are actually made, defaults to False
        :type dry_run: bool (optional)
        :param follow: A boolean flag indicating whether to follow the logs in real-time, defaults to False
        :type follow: bool (optional)
        :param no_color: A boolean flag indicating whether to disable color output in the logs. If set to
        True, the logs will be displayed without any color formatting, defaults to False
        :type no_color: bool (optional)
        :param no_log_prefix: A boolean flag indicating whether to exclude the log prefix in the output. If
        set to True, the log prefix will be omitted, defaults to False
        :type no_log_prefix: bool (optional)
        :param since: The `since` parameter is used to specify the start time for retrieving logs. It
        accepts a string value representing a time duration. For example, "10s" means logs from the last 10
        seconds, "1h" means logs from the last 1 hour, and so on. If
        :type since: Union[None, str]
        :param tail: The `tail` parameter specifies the number of lines to show from the end of the logs. If
        set to `None`, it will show all the logs
        :type tail: Union[None, int]
        :param until: The `until` parameter specifies a timestamp or duration to limit the logs output
        until. It can be either a timestamp in the format `YYYY-MM-DDTHH:MM:SS` or a duration in the format
        `10s`, `5m`, `2h`, etc
        :type until: Union[None, int]
        :param timestamps: A boolean flag indicating whether to show timestamps in the log output, defaults
        to False
        :type timestamps: bool (optional)
        :param stream: A boolean flag indicating whether to stream the logs in real-time or not. If set to
        True, the logs will be continuously streamed as they are generated. If set to False, the logs will
        be displayed once and the function will return, defaults to False
        :type stream: bool (optional)
        :param stream_only_exit_code: A boolean flag indicating whether to only stream the exit code of the
        logs command, defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: The function `logs` returns an iterator.
        """
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
        """
        The `ls` function is a Python method that runs the `ls` command in Docker Compose and returns the
        output.
        
        :param all: A boolean flag indicating whether to show hidden files and directories, defaults to
        False
        :type all: bool (optional)
        :param dry_run: The `dry_run` parameter is a boolean flag that indicates whether the `ls` command
        should be executed as a dry run. A dry run means that the command will be simulated and no actual
        changes will be made, defaults to False
        :type dry_run: bool (optional)
        :param format: The `format` parameter specifies the output format for the `ls` command. It can be
        either "table" or "json", defaults to table
        :type format: Literal["table", "json"] (optional)
        :param quiet: A boolean flag indicating whether to suppress all output except for errors. If set to
        True, the command will not display any output except for error messages, defaults to False
        :type quiet: bool (optional)
        :return: the output of the `ls` command as a string.
        """
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
        """
        The `pull` function is used to pull Docker images, with various options for customization.
        
        :param dry_run: A boolean flag indicating whether the pull operation should be performed as a dry
        run (without actually pulling the images), defaults to False
        :type dry_run: bool (optional)
        :param ignore_buildable: A boolean flag indicating whether to ignore services that are marked as
        "buildable" in the docker-compose file, defaults to False
        :type ignore_buildable: bool (optional)
        :param ignore_pull_failures: A boolean flag indicating whether to ignore failures when pulling
        images. If set to True, any failures encountered during the pull process will be ignored and the
        pull operation will continue. If set to False, any failures will cause the pull operation to stop
        and an error will be raised, defaults to False
        :type ignore_pull_failures: bool (optional)
        :param include_deps: A boolean flag indicating whether to include dependencies when pulling images.
        If set to True, it will pull images for all services and their dependencies. If set to False, it
        will only pull images for the specified services, defaults to False
        :type include_deps: bool (optional)
        :param quiet: A boolean flag indicating whether to suppress output from the command. If set to True,
        the command will be executed quietly without printing any output, defaults to False
        :type quiet: bool (optional)
        :param stream: A boolean flag indicating whether to stream the output of the pull command, defaults
        to False
        :type stream: bool (optional)
        :param stream_only_exit_code: A boolean flag indicating whether to only return the exit code of the
        command when streaming, defaults to False
        :type stream_only_exit_code: bool (optional)
        :return: an iterator.
        """
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
