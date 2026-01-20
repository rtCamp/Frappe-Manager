from subprocess import Popen, run, TimeoutExpired, CalledProcessError
from pathlib import Path
from typing import Iterable, List, Union, Literal, Optional, Tuple, Callable, TypeVar, overload
from functools import wraps
import inspect

import shlex

from frappe_manager.utils.docker import (
    SubprocessOutput,
    parameters_to_options,
    run_command_with_exit_code,
)


T = TypeVar('T')


def docker_command(
    subcommand: str,
    exclude_params: List[str] = None,
    positional_params: List[str] = None
) -> Callable[[T], T]:
    """
    Decorator to handle Docker command parameter conversion.
    
    This decorator eliminates repetitive parameter handling code by automatically:
    - Converting method parameters to docker CLI options
    - Handling positional vs keyword arguments
    - Managing the 'stream' parameter consistently
    - Building and executing docker commands
    
    Args:
        subcommand: The docker/docker-compose subcommand (e.g., "up", "down")
        exclude_params: Parameters to exclude from --flag conversion (default: [])
        positional_params: Parameters to add as positional arguments (default: [])
    
    Example:
        @docker_command("up", positional_params=["services"])
        def up(self, services: list[str] = [], detach: bool = True, stream: bool = False):
            pass  # Implementation handled by decorator
    
    The decorator will automatically:
    - Extract 'services' as positional args: ["up", "service1", "service2"]
    - Convert 'detach' to option: ["--detach"]
    - Handle 'stream' parameter for output control
    """
    if exclude_params is None:
        exclude_params = []
    if positional_params is None:
        positional_params = []
    
    # Always exclude 'stream' as it's handled specially by wrapper
    # Note: 'self' is handled by parameters_to_options internally
    if 'stream' not in exclude_params:
        exclude_params = exclude_params + ['stream']
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            sig = inspect.signature(func)
            bound = sig.bind(self, *args, **kwargs)
            bound.apply_defaults()
            
            parameters = dict(bound.arguments)
            stream = parameters.get('stream', False)
            
            cmd: list = [subcommand]
            
            for param_name in positional_params:
                if param_name in parameters:
                    param_value = parameters[param_name]
                    if isinstance(param_value, list):
                        cmd.extend(param_value)
                    elif param_value is not None:
                        cmd.append(str(param_value))
            
            # Convert keyword parameters to options (e.g., detach=True -> --detach)
            # Note: parameters_to_options will handle removing 'self' internally
            cmd += parameters_to_options(
                parameters, 
                exclude=exclude_params + positional_params
            )
            
            # Execute command using the instance's docker_compose_cmd
            full_cmd = self.docker_compose_cmd + cmd
            iterator = run_command_with_exit_code(full_cmd, stream=stream)
            return iterator
        
        return wrapper
    return decorator


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
        
        # Context manager support - None means no cleanup, empty list means cleanup all
        self._context_services: Optional[List[str]] = None

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
        *,
        stream: Literal[True],
    ) -> Iterable[Tuple[str, bytes]]: ...
    
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
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        """Start services defined in the compose file.
        
        The implementation is handled by the @docker_command decorator which:
        - Adds 'services' as positional arguments
        - Converts remaining parameters to CLI options
        - Executes the docker compose up command
        """
        pass  # Implementation handled by decorator

    @overload
    def down(
        self,
        timeout: int = 100,
        remove_orphans: bool = False,
        rmi: Union[bool, Literal["all", "local"]] = False,
        volumes: bool = False,
        dry_run: bool = False,
        *,
        stream: Literal[True],
    ) -> Iterable[Tuple[str, bytes]]: ...
    
    @overload
    def down(
        self,
        timeout: int = 100,
        remove_orphans: bool = False,
        rmi: Union[bool, Literal["all", "local"]] = False,
        volumes: bool = False,
        dry_run: bool = False,
        *,
        stream: Literal[False] = False,
    ) -> SubprocessOutput: ...

    def down(
        self,
        timeout: int = 100,
        remove_orphans: bool = False,
        rmi: Union[bool, Literal["all", "local"]] = False,
        volumes: bool = False,
        dry_run: bool = False,
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        """Stop and remove containers, networks, images, and volumes.
        
        Note: This method has custom handling for 'rmi' parameter and cannot 
        use the decorator yet. The 'rmi' parameter needs special logic:
        - If False: exclude from command
        - If True or string: convert to --rmi=all
        """
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        down_cmd: list[str] = ["down"]

        remove_parameters = ["stream"]

        if not rmi:
            remove_parameters.append("rmi")
        else:
            parameters["rmi"] = "all"

        down_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        iterator = run_command_with_exit_code(self.docker_compose_cmd + down_cmd, stream=stream)
        return iterator

    @overload
    def start(
        self,
        services: Union[None, list[str]] = None,
        dry_run: bool = False,
        *,
        stream: Literal[True],
    ) -> Iterable[Tuple[str, bytes]]: ...
    
    @overload
    def start(
        self,
        services: Union[None, list[str]] = None,
        dry_run: bool = False,
        *,
        stream: Literal[False] = False,
    ) -> SubprocessOutput: ...

    def start(
        self,
        services: Union[None, list[str]] = None,
        dry_run: bool = False,
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        parameters: dict = locals()

        start_cmd: list[str] = ["start"]

        remove_parameters = ["services", "stream"]

        start_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        # doesn't checks if service exists or not
        if type(services) == list:
            start_cmd += services

        iterator = run_command_with_exit_code(self.docker_compose_cmd + start_cmd, stream=stream)
        return iterator

    def restart(
        self,
        services: Union[None, list[str]] = None,
        dry_run: bool = False,
        timeout: int = 100,
        no_deps: bool = False,
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        parameters: dict = locals()

        parameters["timeout"] = str(timeout)

        restart_cmd: list[str] = ["restart"]

        remove_parameters = ["services", "stream"]

        restart_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        # doesn't checks if service exists or not
        if type(services) == list:
            restart_cmd += services

        iterator = run_command_with_exit_code(self.docker_compose_cmd + restart_cmd, stream=stream)
        return iterator

    def stop(
        self,
        services: Union[None, list[str]] = None,
        timeout: int = 100,
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        parameters: dict = locals()
        parameters["timeout"] = str(timeout)

        stop_cmd: list[str] = ["stop"]

        remove_parameters = ["services", "stream"]

        stop_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if type(services) == list:
            stop_cmd.extend(services)

        iterator = run_command_with_exit_code(self.docker_compose_cmd + stop_cmd, stream=stream)
        return iterator

    @overload
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
        stream: Literal[True] = ...,
        capture_output: bool = True,
        use_shlex_split: bool = True,
    ) -> Iterable[Tuple[str, bytes]]: ...
    
    @overload
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
        stream: Literal[False] = False,
        capture_output: bool = True,
        use_shlex_split: bool = True,
    ) -> SubprocessOutput: ...

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
        capture_output: bool = True,
        use_shlex_split: bool = True,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        parameters: dict = locals()

        exec_cmd: list[str] = ["exec"]

        remove_parameters = ["service", "stream", "command", "env", "use_shlex_split", "capture_output", "no_tty"]

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
            self.docker_compose_cmd + exec_cmd, stream=stream, capture_output=capture_output
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
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        parameters: dict = locals()

        ps_cmd: list[str] = ["ps"]

        remove_parameters = [
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

        iterator = run_command_with_exit_code(self.docker_compose_cmd + ps_cmd, stream=stream)
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
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        parameters: dict = locals()

        logs_cmd: list[str] = ["logs"]

        remove_parameters = ["services", "stream"]

        logs_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        if services:
            logs_cmd += services

        iterator = run_command_with_exit_code(
            self.docker_compose_cmd + logs_cmd,
            stream=stream,
        )
        return iterator

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
        except:
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
    ) -> Iterable[Tuple[str, bytes]]: ...
    
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
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        """Pull service images defined in the compose file.
        
        Implementation handled by @docker_command decorator.
        """
        pass  # Implementation handled by decorator

    def run(
        self,
        service: str,
        command: Optional[str] = None,
        name: Optional[str] = None,
        user: Optional[str] = None,
        detach: bool = False,
        rm: bool = False,
        entrypoint: Optional[str] = None,
        use_shlex_split: bool = True,
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
        parameters: dict = locals()
        run_cmd: list = ["run"]

        remove_parameters = ["stream", "command", "service", "use_shlex_split"]

        run_cmd += parameters_to_options(parameters, exclude=remove_parameters)

        run_cmd += [service]

        if command:
            if use_shlex_split:
                run_cmd += shlex.split(command, posix=True)
            else:
                run_cmd += [command]

        iterator = run_command_with_exit_code(self.docker_compose_cmd + run_cmd, stream=stream)
        return iterator

    def cp(
        self,
        source: str,
        destination: str,
        source_container: str = None,
        destination_container: str = None,
        archive: bool = False,
        follow_link: bool = False,
        stream: bool = False,
    ) -> Union[Iterable[Tuple[str, bytes]], SubprocessOutput]:
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

        iterator = run_command_with_exit_code(self.docker_compose_cmd + cp_cmd, stream=stream)
        return iterator

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
                if status.get('State') == 'running':
                    return True
            return False
        except Exception:
            return False

    def get_service_status(self, service: str) -> Optional[dict]:
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

    def get_all_services_status(self) -> List[dict]:
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

    def wait_for_service(
        self,
        service: str,
        timeout: int = 30,
        check_interval: float = 0.5
    ) -> bool:
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

    def exec_capture(
        self,
        service: str,
        command: str,
        **kwargs
    ) -> SubprocessOutput:
        """
        Execute command and capture output (stream=False wrapper).
        
        Args:
            service: Service name
            command: Command to execute
            **kwargs: Additional exec arguments
        
        Returns:
            SubprocessOutput with stdout/stderr/exit_code
        """
        kwargs['stream'] = False
        return self.exec(service=service, command=command, **kwargs)

    def exec_stream(
        self,
        service: str,
        command: str,
        **kwargs
    ) -> Iterable[Tuple[str, bytes]]:
        """
        Execute command and stream output (stream=True wrapper).
        
        Args:
            service: Service name
            command: Command to execute
            **kwargs: Additional exec arguments
        
        Returns:
            Iterator of (source, line) tuples
        """
        kwargs['stream'] = True
        return self.exec(service=service, command=command, **kwargs)

    # Context Manager Support
    
    def __enter__(self) -> 'DockerComposeWrapper':
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
    
    def with_auto_cleanup(self, services: Optional[List[str]] = None) -> 'DockerComposeWrapper':
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
