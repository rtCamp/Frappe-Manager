from pathlib import Path

from rich.box import Box

from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.utils import helpers


class BenchException(FrappeManagerException):
    """Base exception for all bench-related errors."""

    def __init__(
        self,
        bench_name: str,
        message: str,
        prefix_bench_name: bool = True,
    ):
        self.message = message

        if prefix_bench_name:
            self.message = f"[fm.info][bold]{bench_name} :[/bold][/fm.info] {message}"

        super().__init__(self.message)


class BenchServiceNotRunning(BenchException):
    """Raised when a required bench service is not running."""

    def __init__(
        self,
        bench_name: str,
        service: str,
        message: str = "Service {} not running.",
    ):
        self.bench_name = bench_name
        self.service = service
        self.message = message.format(self.service)
        super().__init__(self.bench_name, self.message)


class BenchNotFoundError(FileNotFoundError, BenchException):
    """Raised when bench directory is not found at expected location."""

    def __init__(
        self,
        bench_name: str,
        path: Path,
        message: str = "Bench not found at {}.",
    ):
        self.bench_name = bench_name
        self.path = path
        self.message = message.format(self.path)
        # Chain explicitly, NOT via super(). `FileNotFoundError` precedes `BenchException` in
        # the MRO, so `super().__init__(name, message)` reaches `OSError.__init__`, which reads
        # two positional args as (errno, strerror) and renders the bench name as a bogus
        # "[Errno nope.localhost]" prefix. It also skips `FrappeManagerException.__init__`
        # entirely, leaving no `.details`, which the top-level handler in main.py reads -- so
        # every "bench not found" ended in an AttributeError traceback and logged nothing.
        BenchException.__init__(self, self.bench_name, self.message)


class BenchRemoveDirectoryError(BenchException):
    """Raised when bench directory removal fails."""

    def __init__(
        self,
        bench_name: str,
        path: Path,
        message: str = "Remove dirs failed at {}.",
    ):
        self.bench_name = bench_name
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.bench_name, self.message)


class BenchWorkersSupervisorConfigurtionNotFoundError(BenchException):
    """Raised when supervisor worker configuration file is not found."""

    def __init__(
        self,
        bench_name: str,
        config_dir: str,
        message: str = "Superviosrd workers configuration not found in {}.",
    ):
        self.bench_name = bench_name
        self.config_dir = config_dir
        self.message = message.format(self.config_dir)
        super().__init__(self.bench_name, self.message)


class AdminToolsFailedToStart(BenchException):
    """Raised when admin tools (mailpit, adminer, redis-queue-dashboard) fail to start.

    ``compose_path`` and ``services`` carry the same context the old
    ``DockerComposeProjectFailedToStartError`` did, so callers keep naming what failed. That
    class lived in ``frappe_manager/compose_project/``, a directory holding nothing but its
    own exceptions file after the ``ComposeProject`` class it served was removed. Admin tools
    were its only caller, and this file already owned the concern.
    """

    def __init__(self, bench_name, compose_path=None, services=None, message="Failed to start admin tools"):
        self.bench_name = bench_name
        self.compose_path = compose_path
        self.services = list(services or [])
        named = f" ({', '.join(self.services)})" if self.services else ""
        self.message = f"{message.rstrip('.')}{named}."
        super().__init__(self.bench_name, self.message)


class AdminToolsFailedToStop(BenchException):
    """Raised when admin tools containers fail to stop."""

    def __init__(self, bench_name, compose_path=None, services=None, message="Failed to stop admin tools"):
        self.bench_name = bench_name
        self.compose_path = compose_path
        self.services = list(services or [])
        named = f" ({', '.join(self.services)})" if self.services else ""
        self.message = f"{message.rstrip('.')}{named}."
        super().__init__(self.bench_name, self.message)


class BenchSSLCertificateAlreadyIssued(BenchException):
    """Raised when attempting to issue an SSL certificate that already exists."""

    def __init__(self, bench_name, message="SSL Certificate already issued."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchSSLCertificateNotIssued(BenchException):
    """Raised when SSL certificate operation requires an issued certificate but none exists."""

    def __init__(self, bench_name, message="No SSL Certificate issued."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchAttachTocontainerFailed(BenchException):
    """Raised when attaching to a container fails."""

    def __init__(self, bench_name, service_name, message="Attach to {} service container failed."):
        self.bench_name = bench_name
        self.service_name = service_name
        self.message = message.format(self.service_name)
        super().__init__(self.bench_name, self.message)


class BenchNotRunning(BenchException):
    """Raised when bench services are required to be running but are not."""

    def __init__(self, bench_name, message="Bench services not running."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchFailedToRemoveDevPackages(BenchException):
    """Raised when pip uninstall of development packages fails."""

    def __init__(self, bench_name, message="Not able pip uninstall dev packages."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchFailedToInstallDevPackages(BenchException):
    """Raised when pip install of development packages fails."""

    def __init__(self, bench_name, message="Not able pip install dev packages."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchOperationException(BenchException):
    """Base exception for bench operations that may include subprocess output."""

    def __init__(
        self,
        bench_name,
        message: str,
        print_combined: bool = True,
        print_stdout: bool = False,
        print_stderr: bool = False,
    ):
        self.bench_name = bench_name
        self.message = message
        self.print_stdout = print_stdout
        self.print_stderr = print_stderr
        self.print_combined = print_combined
        self.output = None
        super().__init__(self.bench_name, self.message)

    def set_output(self, output: SubprocessOutput):
        self.output = output
        from rich.panel import Panel

        to_print = []

        box: Box = Box("╭   \n    \n ── \n│   \n    \n    \n |  \n    \n", ascii=True)

        if self.print_stdout:
            panel = Panel.fit(
                "\n".join(self.output.stdout),
                box=box,
                padding=(0, 1),
                border_style="fm.muted",
                title="Error command stdout",
                title_align="left",
            )
            to_print.append(helpers.rich_object_to_string(panel))

        if self.print_combined:
            panel = Panel.fit(
                "\n".join(self.output.combined),
                box=box,
                padding=(0, 1),
                border_style="fm.muted",
                title="Error command output",
                title_align="left",
            )
            to_print.append(helpers.rich_object_to_string(panel))

        if self.print_stderr:
            panel = Panel.fit(
                "\n".join(self.output.stderr),
                box=box,
                padding=(0, 1),
                border_style="fm.muted",
                title="Error command stderr",
                title_align="left",
            )
            to_print.append(helpers.rich_object_to_string(panel))

        self.message = self.message + "\n" + "\n".join(to_print)

        super().__init__(self.bench_name, self.message, prefix_bench_name=False)


class BenchOperationRequiredDockerImagesNotAvailable(BenchException):
    """Raised when required Docker images are not available locally."""

    def __init__(
        self,
        bench_name,
        pull_command,
        message: str = "Required docker images not available. Pull all required images using command '{}'.",
    ):
        self.bench_name = bench_name
        self.message = message.format(pull_command)
        super().__init__(self.bench_name, self.message)


class BenchOperationBenchInstallAppInPythonEnvFailed(BenchOperationException):
    """Raised when installing an app in the Python environment fails."""

    def __init__(
        self,
        bench_name,
        app_name: str,
        message: str = "Failed to install app {} in python env.",
        print_combined: bool = True,
        print_stdout: bool = False,
        print_stderr: bool = False,
    ):
        self.bench_name = bench_name
        self.app_name = app_name
        self.message = message.format(app_name)
        self.print_stdout = print_stdout
        self.print_stderr = print_stderr
        self.print_combined = print_combined

        super().__init__(self.bench_name, self.message, self.print_combined, self.print_stdout, self.print_stderr)


class BenchOperationWaitForRequiredServiceFailed(BenchOperationException):
    """Raised when waiting for a required service to become available times out."""

    def __init__(
        self,
        bench_name,
        host: str,
        port: str,
        timeout: int,
        message: str = "Waiting for service {}:{} timed out. {}",
        print_combined: bool = True,
        print_stdout: bool = False,
        print_stderr: bool = False,
    ):
        self.bench_name = bench_name
        self.host = host
        self.port = port
        self.timeout = timeout
        self.print_stdout = print_stdout
        self.print_stderr = print_stderr
        self.print_combined = print_combined
        self.message = message.format(host, port, timeout)

        super().__init__(self.bench_name, self.message, self.print_combined, self.print_stdout, self.print_stderr)


class BenchOperationBenchSiteCreateFailed(BenchOperationException):
    """Raised when bench site creation fails."""

    def __init__(
        self,
        bench_name,
        print_combined: bool = True,
        print_stdout: bool = False,
        print_stderr: bool = False,
        message: str = "Failed to create site {}.",
    ):
        self.bench_name = bench_name
        self.message = message.format(bench_name)
        self.print_stdout = print_stdout
        self.print_stderr = print_stderr
        self.print_combined = print_combined
        super().__init__(self.bench_name, self.message, self.print_combined, self.print_stdout, self.print_stderr)


class BenchOperationBenchRemoveAppFromPythonEnvFailed(BenchOperationException):
    """Raised when removing an app from the Python environment fails."""

    def __init__(
        self,
        bench_name,
        app_name: str,
        message: str = "Failed to remove app {} from python env.",
        print_combined: bool = True,
        print_stdout: bool = False,
        print_stderr: bool = False,
    ):
        self.bench_name = bench_name
        self.app_name = app_name
        self.message = message.format(app_name)
        self.print_stdout = print_stdout
        self.print_stderr = print_stderr
        self.print_combined = print_combined

        super().__init__(self.bench_name, self.message, self.print_combined, self.print_stdout, self.print_stderr)


class BenchOperationBenchAppInSiteFailed(BenchOperationException):
    """Raised when installing an app in a site fails."""

    def __init__(
        self,
        bench_name,
        app_name: str,
        message: str = "Failed to install app {} in site {}.",
        print_combined: bool = True,
        print_stdout: bool = False,
        print_stderr: bool = False,
    ):
        self.bench_name = bench_name
        self.app_name = app_name
        self.message = message.format(app_name, self.bench_name)
        self.print_stdout = print_stdout
        self.print_stderr = print_stderr
        self.print_combined = print_combined
        super().__init__(self.bench_name, self.message, self.print_combined, self.print_stdout, self.print_stderr)


class BenchOperationBenchBuildFailed(BenchOperationException):
    """Raised when bench build operation fails."""

    def __init__(
        self,
        bench_name,
        apps: list[str] | None = None,
        message: str = "Failed to build",
        print_combined: bool = True,
        print_stdout: bool = False,
        print_stderr: bool = False,
    ):
        self.bench_name = bench_name
        self.apps = apps
        if apps:
            message = message + " app"
            if len(apps) > 1:
                message = message + " apps"
            for app in apps:
                message += f" {app}"
        self.message = message
        self.print_stdout = print_stdout
        self.print_stderr = print_stderr
        self.print_combined = print_combined
        super().__init__(self.bench_name, self.message, self.print_combined, self.print_stdout, self.print_stderr)
