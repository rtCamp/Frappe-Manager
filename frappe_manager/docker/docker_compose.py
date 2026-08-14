import inspect
import itertools
import shlex
from collections.abc import Callable, Iterable
from functools import wraps
from pathlib import Path
from subprocess import run
from typing import Literal, TypeVar, cast, overload

from frappe_manager.docker import DOCKER_LINE_NOISE
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.utils.docker import (
    SubprocessOutput,
    parameters_to_options,
    run_command_with_exit_code,
)

T = TypeVar("T")


def docker_command(
    subcommand: str,
    exclude_params: list[str] | None = None,
    positional_params: list[str] | None = None,
    use_original_implementation: bool = False,
) -> Callable[[T], T]:
    if exclude_params is None:
        exclude_params = []
    if positional_params is None:
        positional_params = []

    if "stream" not in exclude_params:
        exclude_params = exclude_params + ["stream"]

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            sig = inspect.signature(func)
            bound = sig.bind(self, *args, **kwargs)
            bound.apply_defaults()

            parameters = dict(bound.arguments)
            stream_param = parameters.get("stream")

            should_stream = (
                stream_param
                if stream_param is not None
                else (self.output.should_stream_docker if self.output else False)
            )

            if use_original_implementation:
                full_cmd = func(self, *args, **kwargs)
            else:
                cmd: list = [subcommand]

                for param_name in positional_params:
                    if param_name in parameters:
                        param_value = parameters[param_name]
                        if isinstance(param_value, list):
                            cmd.extend(param_value)
                        elif param_value is not None:
                            cmd.append(str(param_value))

                cmd += parameters_to_options(parameters, exclude=exclude_params + positional_params)

                full_cmd = self.docker_compose_cmd + cmd

            if should_stream:
                from frappe_manager.logger import get_logger

                logger = get_logger(component="docker_compose")
                logger.debug(
                    f"[docker_command] Auto-streaming: should_stream={should_stream}, stream_param={stream_param}, output={self.output is not None}",
                )

                stream_result = run_command_with_exit_code(full_cmd, stream=True)
                iterator = cast("Iterable[tuple[str, bytes]]", stream_result)
                if self.output and stream_param is None:
                    logger.debug("[docker_command] Entering auto-streaming mode with tee()")
                    display_iter, capture_iter = itertools.tee(iterator, 2)
                    logger.debug("[docker_command] Calling output.live_lines()")
                    self.output.live_lines(display_iter, padding=(0, 0, 0, 2), line_filters=DOCKER_LINE_NOISE)
                    logger.debug("[docker_command] Converting capture_iter to SubprocessOutput")
                    result = SubprocessOutput.from_output(capture_iter)
                    logger.debug(
                        f"[docker_command] Returning SubprocessOutput: exit_code={result.exit_code}, stdout_lines={len(result.stdout)}, stderr_lines={len(result.stderr)}",
                    )
                    return result
                logger.debug("[docker_command] Returning raw iterator (explicit stream=True)")
                return iterator
            result = run_command_with_exit_code(full_cmd, stream=False)
            return result

        return wrapper

    return decorator


def _build_cp_cmd(
    source: str,
    destination: str,
    source_container: str | None,
    destination_container: str | None,
    archive: bool,
    follow_link: bool,
) -> list:
    """Build the `cp` subcommand argv shared by DockerComposeWrapper.cp and DockerClient.cp.

    The two wrappers differ only in the command prefix they prepend and in who executes the
    result, so only the subcommand is shared: `cp`, the option flags in signature order, then
    the two positionals with any container name folded into them.
    """
    cp_cmd: list = ["cp"]

    cp_cmd += parameters_to_options({"archive": archive, "follow_link": follow_link})

    if source_container:
        source = f"{source_container}:{source}"

    if destination_container:
        destination = f"{destination_container}:{destination}"

    cp_cmd += [f"{source}"]
    cp_cmd += [f"{destination}"]

    return cp_cmd


# Docker Compose version 2.18.1
class DockerComposeWrapper:
    """
    This class provides one to one mapping between docker compose cli each function.
    Only this args have are different use case.

    Args:
        stream (bool, optional): A boolean flag indicating whether to stream the output of the command as it runs.
            If set to True, the output will be displayed in real-time. If set to False, the output will be
            displayed after the command completes. Defaults to False.
    """

    def __init__(self, path: Path, timeout: int = 100, output: OutputHandler | None = None):
        self.compose_file_path = path.absolute()
        self.output = output

        self.docker_compose_cmd = [
            "docker",
            "compose",
            "-f",
            self.compose_file_path.as_posix(),
        ]

        # User-owned override: an adjacent `<name>.override.yml` (fm never writes it) is
        # deep-merged by Docker after the base, so hand-authored customizations survive every
        # fm regeneration of the base compose. Appended after the base so the override wins,
        # and inherited by every subcommand via this shared command prefix.
        override_path = self.compose_file_path.with_name(
            f"{self.compose_file_path.stem}.override{self.compose_file_path.suffix}"
        )
        if override_path.exists():
            self.docker_compose_cmd += ["-f", override_path.as_posix()]

        self._context_services: list[str] | None = None

    @overload
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
        wait: bool = False,
        *,
        stream: Literal[True],
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
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
        wait: bool = False,
        *,
        stream: Literal[False] = False,
    ) -> SubprocessOutput: ...

    @docker_command("up", positional_params=["services"])
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
        wait: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        """Start services defined in the compose file.

        The implementation is handled by the @docker_command decorator which:
        - Adds 'services' as positional arguments
        - Converts remaining parameters to CLI options
        - Executes the docker compose up command
        """
        # Implementation handled by decorator

    @overload
    def down(
        self,
        timeout: int = 100,
        remove_orphans: bool = False,
        rmi: bool | Literal["all", "local"] = False,
        volumes: bool = False,
        dry_run: bool = False,
        *,
        stream: Literal[True],
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def down(
        self,
        timeout: int = 100,
        remove_orphans: bool = False,
        rmi: bool | Literal["all", "local"] = False,
        volumes: bool = False,
        dry_run: bool = False,
        *,
        stream: Literal[False] = False,
    ) -> SubprocessOutput: ...

    @docker_command(subcommand="down", use_original_implementation=True)
    def down(
        self,
        timeout: int = 100,
        remove_orphans: bool = False,
        rmi: bool | Literal["all", "local"] = False,
        volumes: bool = False,
        dry_run: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        down_cmd: list[str] = ["down"]
        remove_parameters = ["stream", "self"]

        if not rmi:
            remove_parameters.append("rmi")
        else:
            parameters["rmi"] = "all"

        down_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        return self.docker_compose_cmd + down_cmd

    @overload
    def start(
        self,
        services: None | list[str] = None,
        dry_run: bool = False,
        *,
        stream: Literal[True],
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def start(
        self,
        services: None | list[str] = None,
        dry_run: bool = False,
        *,
        stream: Literal[False] = False,
    ) -> SubprocessOutput: ...

    @docker_command(subcommand="start", positional_params=["services"])
    def start(
        self,
        services: None | list[str] = None,
        dry_run: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        pass

    @docker_command(subcommand="restart", use_original_implementation=True)
    def restart(
        self,
        services: None | list[str] = None,
        dry_run: bool = False,
        timeout: int = 100,
        no_deps: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        restart_cmd: list[str] = ["restart"]
        remove_parameters = ["services", "stream", "self"]

        restart_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(services) == list:
            restart_cmd += services

        return self.docker_compose_cmd + restart_cmd

    @docker_command(subcommand="stop", use_original_implementation=True)
    def stop(
        self,
        services: None | list[str] = None,
        timeout: int = 100,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        stop_cmd: list[str] = ["stop"]
        remove_parameters = ["services", "stream", "self"]

        stop_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(services) == list:
            stop_cmd.extend(services)

        return self.docker_compose_cmd + stop_cmd

    @docker_command(subcommand="rm", use_original_implementation=True)
    def rm(
        self,
        services: None | list[str] = None,
        force: bool = False,
        stop: bool = False,
        volumes: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        """Remove stopped (or, with ``stop=True``, running) service containers.

        Unlike ``down --remove-orphans`` this touches ONLY the named services,
        which matters because fm's bench compose files share one directory and
        therefore one compose project: orphan removal on any of them removes
        every other file's containers.
        """
        parameters: dict = locals()

        rm_cmd: list[str] = ["rm"]
        remove_parameters = ["services", "stream", "self"]

        rm_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(services) == list:
            rm_cmd.extend(services)

        return self.docker_compose_cmd + rm_cmd

    @overload
    def exec(
        self,
        service: str,
        command: str,
        detach: bool = False,
        env: None | list[str] = None,
        no_tty: bool = False,
        privileged: bool = False,
        user: None | str = None,
        workdir: None | str = None,
        stream: Literal[True] = ...,
        capture_output: bool = True,
        use_shlex_split: bool = True,
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def exec(
        self,
        service: str,
        command: str,
        detach: bool = False,
        env: None | list[str] = None,
        no_tty: bool = False,
        privileged: bool = False,
        user: None | str = None,
        workdir: None | str = None,
        stream: Literal[False] = False,
        capture_output: bool = True,
        use_shlex_split: bool = True,
    ) -> SubprocessOutput: ...

    @docker_command(subcommand="exec", use_original_implementation=True)
    def exec(
        self,
        service: str,
        command: str,
        detach: bool = False,
        env: None | list[str] = None,
        no_tty: bool = False,
        privileged: bool = False,
        user: None | str = None,
        workdir: None | str = None,
        stream: bool | None = None,
        capture_output: bool = True,
        use_shlex_split: bool = True,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        parameters: dict = locals()

        exec_cmd: list[str] = ["exec"]

        remove_parameters = [
            "self",
            "service",
            "stream",
            "command",
            "env",
            "use_shlex_split",
            "capture_output",
            "no_tty",
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

        return self.docker_compose_cmd + exec_cmd

    @docker_command(subcommand="ps", use_original_implementation=True)
    def ps(
        self,
        service: None | list[str] = None,
        dry_run: bool = False,
        all: bool = False,
        services: bool = False,
        filter: None | Literal["paused", "restarting", "removing", "running", "dead", "created", "exited"] = None,
        format: None | Literal["table", "json"] = None,
        status: None | list[Literal["paused", "restarting", "removing", "running", "dead", "created", "exited"]] = None,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        parameters: dict = locals()

        ps_cmd: list[str] = ["ps"]

        remove_parameters = [
            "self",
            "service",
            "stream",
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

        return self.docker_compose_cmd + ps_cmd

    @docker_command(subcommand="logs", use_original_implementation=True)
    def logs(
        self,
        services: None | list[str] = None,
        dry_run: bool = False,
        follow: bool = False,
        no_color: bool = False,
        no_log_prefix: bool = False,
        since: None | str = None,
        tail: None | int = None,
        until: None | int = None,
        timestamps: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        parameters: dict = locals()

        logs_cmd: list[str] = ["logs"]

        remove_parameters = ["self", "services", "stream"]

        logs_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if services:
            logs_cmd += services

        return self.docker_compose_cmd + logs_cmd

    def ls(
        self,
        all: bool = False,
        dry_run: bool = False,
        format: Literal["table", "json"] = "table",
    ):
        parameters: dict = locals()

        ls_cmd: list[str] = ["ls"]

        ls_cmd += parameters_to_options(parameters)

        try:
            output = run(self.docker_compose_cmd + ls_cmd, capture_output=True)
            output = output.stdout.decode()
        except Exception:
            return False

        return output

    @overload
    def pull(
        self,
        dry_run: bool = False,
        ignore_buildable: bool = False,
        ignore_pull_failures: bool = False,
        include_deps: bool = False,
        *,
        stream: Literal[True],
    ) -> Iterable[tuple[str, bytes]]: ...

    @overload
    def pull(
        self,
        dry_run: bool = False,
        ignore_buildable: bool = False,
        ignore_pull_failures: bool = False,
        include_deps: bool = False,
        *,
        stream: Literal[False] = False,
    ) -> SubprocessOutput: ...

    @docker_command("pull")
    def pull(
        self,
        dry_run: bool = False,
        ignore_buildable: bool = False,
        ignore_pull_failures: bool = False,
        include_deps: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        """Pull service images defined in the compose file.

        Implementation handled by @docker_command decorator.
        """
        # Implementation handled by decorator

    @docker_command(subcommand="run", use_original_implementation=True)
    def run(
        self,
        service: str,
        command: str | None = None,
        name: str | None = None,
        user: str | None = None,
        detach: bool = False,
        rm: bool = False,
        entrypoint: str | None = None,
        env: None | list[str] = None,
        use_shlex_split: bool = True,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        parameters: dict = locals()
        run_cmd: list = ["run"]

        remove_parameters = ["stream", "command", "service", "use_shlex_split", "self", "env"]

        run_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        # One --env per KEY=VALUE, before the service name, exactly as exec does.
        # An --env value is visible in the container's process listing, so nothing
        # secret travels this way: fm sends MYSQL_HOME, which is a path.
        if type(env) == list:
            for i in env:
                run_cmd += ["--env", i]

        run_cmd += [service]

        if command:
            if use_shlex_split:
                run_cmd += shlex.split(command, posix=True)
            else:
                run_cmd += [command]

        return self.docker_compose_cmd + run_cmd

    @docker_command(subcommand="cp", use_original_implementation=True)
    def cp(
        self,
        source: str,
        destination: str,
        source_container: str | None = None,
        destination_container: str | None = None,
        archive: bool = False,
        follow_link: bool = False,
        stream: bool | None = None,
    ) -> Iterable[tuple[str, bytes]] | SubprocessOutput:
        return self.docker_compose_cmd + _build_cp_cmd(
            source, destination, source_container, destination_container, archive, follow_link
        )

    # ==================== NEW: Convenience Methods ====================

    def is_service_running(self, service: str) -> bool:
        """
        Check if a service is running.

        Args:
            service: Service name to check

        Returns:
            True if service is running, False otherwise
        """
        try:
            output = self.ps(service=[service], format="json", stream=False)
            if not output.stdout:
                return False

            import json

            for line in output.stdout:
                status = json.loads(line)
                if status.get("State") == "running":
                    return True
            return False
        except Exception:
            return False

    def get_service_status(self, service: str) -> dict | None:
        """
        Get detailed status for a service.

        Args:
            service: Service name

        Returns:
            Dict with status info or None if not found
        """
        try:
            output = self.ps(service=[service], format="json", all=True, stream=False)
            if output.stdout:
                import json

                return json.loads(output.stdout[0])
            return None
        except Exception:
            return None

    def get_all_services_status(self) -> list[dict]:
        """
        Get status for all services in compose file.

        Returns:
            List of status dicts
        """
        statuses = []
        try:
            output = self.ps(format="json", all=True, stream=False)
            import json

            for line in output.stdout:
                statuses.append(json.loads(line))
        except Exception:
            pass
        return statuses

    def wait_for_service(self, service: str, timeout: int = 30, check_interval: float = 0.5) -> bool:
        """
        Wait for a service to be running.

        Args:
            service: Service name
            timeout: Max seconds to wait
            check_interval: Seconds between checks

        Returns:
            True if service started, False if timeout
        """
        import time

        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.is_service_running(service):
                return True
            time.sleep(check_interval)

        return False

    def exec_capture(self, service: str, command: str, **kwargs) -> SubprocessOutput:
        """
        Execute command and capture output (stream=False wrapper).

        Args:
            service: Service name
            command: Command to execute
            **kwargs: Additional exec arguments

        Returns:
            SubprocessOutput with stdout/stderr/exit_code
        """
        kwargs["stream"] = False
        return self.exec(service=service, command=command, **kwargs)

    def exec_stream(self, service: str, command: str, **kwargs) -> Iterable[tuple[str, bytes]]:
        """
        Execute command and stream output (stream=True wrapper).

        Args:
            service: Service name
            command: Command to execute
            **kwargs: Additional exec arguments

        Returns:
            Iterator of (source, line) tuples
        """
        kwargs["stream"] = True
        return self.exec(service=service, command=command, **kwargs)

    # Context Manager Support

    def __enter__(self) -> "DockerComposeWrapper":
        """
        Enter context manager - returns self for use in 'with' statement.

        Returns:
            Self for method chaining

        Example:
            with DockerComposeWrapper(path).with_auto_cleanup() as compose:
                compose.up(detach=True, stream=False)
                # Work with compose...
                # Auto-cleanup on exit
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """
        Exit context manager - performs cleanup if services are registered.

        Args:
            exc_type: Exception type (if any)
            exc_val: Exception value (if any)
            exc_tb: Exception traceback (if any)

        Returns:
            False (does not suppress exceptions)

        Note:
            If services were registered via with_auto_cleanup(), this will
            call down() to stop and remove containers. Cleanup errors are
            silently ignored (best-effort cleanup).
        """
        if self._context_services is not None:
            try:
                # Best effort cleanup - don't fail if cleanup fails
                self.down(remove_orphans=True, stream=False)
            except Exception:
                pass  # Silently ignore cleanup errors

        # Don't suppress exceptions - return False
        return False

    def with_auto_cleanup(self, services: list[str] | None = None) -> "DockerComposeWrapper":
        """
        Register services for automatic cleanup when context exits.

        Args:
            services: List of service names to cleanup. If None, all services
                     in the compose file will be cleaned up.

        Returns:
            Self for method chaining

        Example:
            # Cleanup all services
            with DockerComposeWrapper(path).with_auto_cleanup() as compose:
                compose.up(detach=True, stream=False)
                # Auto-cleanup on exit

            # Cleanup specific services
            with DockerComposeWrapper(path).with_auto_cleanup(['redis', 'db']) as compose:
                compose.up(services=['redis', 'db'], detach=True, stream=False)
                # Auto-cleanup on exit

        Note:
            Must be used with context manager (with statement).
            Cleanup happens even if an exception occurs.
        """
        self._context_services = services if services is not None else []
        return self
