from builtins import len
from pathlib import Path
from typing import List, Optional

from frappe_manager.docker_wrapper.subprocess_output import SubprocessOutput
from frappe_manager.utils import helpers
from rich.box import Box
from rich.style import Style
from typer import Option


class BenchException(Exception):
    def __init__(
        self,
        bench_name: str,
        message: str,
        prefix_bench_name: bool = True,
    ):
        self.message = message

        if prefix_bench_name:
            self.message = f"[blue][bold]{bench_name} :[/bold][/blue] {message}"

        super().__init__(self.message)


class BenchDockerComposeFileNotFound(BenchException):
    def __init__(
        self,
        bench_name: str,
        path: Path,
        message: str = "Compose file not found at {}. Aborting operation.",
    ):
        self.bench_name = bench_name
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.bench_name, self.message)


class BenchServiceNotRunning(BenchException):
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


class BenchNotFoundError(BenchException):
    def __init__(
        self,
        bench_name: str,
        path: Path,
        message: str = "Bench not found at {}.",
    ):
        self.bench_name = bench_name
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.bench_name, self.message)


class BenchRemoveDirectoryError(BenchException):
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


class BenchLogFileNotFoundError(BenchException):
    def __init__(
        self,
        bench_name: str,
        path: Path,
        message: str = "Log file not found at {}.",
    ):
        self.bench_name = bench_name
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.bench_name, self.message)


class BenchWorkersStartError(BenchException):
    def __init__(
        self,
        bench_name: str,
        message: str = "Workers not able to start.",
    ):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchWorkersSupervisorConfigurtionGenerateError(BenchException):
    def __init__(
        self,
        bench_name: str,
        message: str = "Failed to configure workers.",
    ):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchWorkersSupervisorConfigurtionNotFoundError(BenchException):
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


class BenchConfigFileNotFound(BenchException):
    def __init__(self, bench_name, config_path, message="Config file not found at {}."):
        self.bench_name = bench_name
        self.config_path = config_path
        self.message = message.format(config_path)
        super().__init__(self.bench_name, self.message)


class BenchConfigValidationError(BenchException):
    def __init__(self, bench_name, config_path, message="FM bench config not valid at {}"):
        self.bench_name = bench_name
        self.conig_path = config_path
        self.message = message.format(self.conig_path)
        super().__init__(self.bench_name, self.message)


class AdminToolsFailedToStart(BenchException):
    def __init__(self, bench_name, message="Failed to start admin tools."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchSSLCertificateAlreadyIssued(BenchException):
    def __init__(self, bench_name, message="SSL Certificate already issued."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchSSLCertificateNotIssued(BenchException):
    def __init__(self, bench_name, message="No SSL Certificate issued."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchAttachTocontainerFailed(BenchException):
    def __init__(self, bench_name, service_name, message="Attach to {} service container failed."):
        self.bench_name = bench_name
        self.service_name = service_name
        self.message = message.format(self.service_name)
        super().__init__(self.bench_name, self.message)


class BenchNotRunning(BenchException):
    def __init__(self, bench_name, message="Bench services not running."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchFailedToRemoveDevPackages(BenchException):
    def __init__(self, bench_name, message="Not able pip uninstall dev packages."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchFrappeServiceSupervisorNotRunning(BenchException):
    def __init__(self, bench_name, message="Supervisorctl is not running in frappe service"):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchOperationException(BenchException):
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

        box: Box = Box("╭   \n" "    \n" " ── \n" "│   \n" "    \n" "    \n" " |  \n" "    \n", ascii=True)

        if self.print_stdout:
            panel = Panel.fit(
                "\n".join(self.output.stdout),
                box=box,
                padding=(0, 1),
                border_style="dim",
                title="Error command stdout",
                title_align="left",
            )
            to_print.append(helpers.rich_object_to_string(panel))

        if self.print_combined:
            panel = Panel.fit(
                "\n".join(self.output.combined),
                box=box,
                padding=(0, 1),
                border_style="dim",
                title="Error command output",
                title_align="left",
            )
            to_print.append(helpers.rich_object_to_string(panel))

        if self.print_stderr:
            panel = Panel.fit(
                "\n".join(self.output.stderr),
                box=box,
                padding=(0, 1),
                border_style="dim",
                title="Error command stderr",
                title_align="left",
            )
            to_print.append(helpers.rich_object_to_string(panel))

        self.message = self.message + "\n" + "\n".join(to_print)

        super().__init__(self.bench_name, self.message, prefix_bench_name=False)


class BenchOperationFrappeBranchChangeFailed(BenchException):
    def __init__(self, bench_name, app: str, branch: str, message: str = "Failed to change {} app branch to {}."):
        self.bench_name = bench_name
        self.app = app
        self.branch = branch
        self.message = message.format(app, branch)
        super().__init__(self.bench_name, self.message)


class BenchOperationRequiredDockerImagesNotAvailable(BenchException):
    def __init__(
        self,
        bench_name,
        pull_command,
        message: str = "Required docker images not available. Pull all required images using command '{}'.",
    ):
        self.bench_name = bench_name
        self.message = message.format(pull_command)
        super().__init__(self.bench_name, self.message)


class BenchOperationWaitForRequiredServiceFailed(BenchOperationException):
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


class BenchOperationBenchInstallAppInPythonEnvFailed(BenchOperationException):
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


class BenchOperationBenchRemoveAppFromPythonEnvFailed(BenchOperationException):
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
    def __init__(
        self,
        bench_name,
        apps: Optional[List[str]] = None,
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
