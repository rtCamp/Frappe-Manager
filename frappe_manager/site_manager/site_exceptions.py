from pathlib import Path

from pydantic import config


class BenchException(Exception):
    def __init__(
        self,
        bench_name: str,
        message: str,
    ):
        self.message = f"[blue][bold]{bench_name} :[/bold][/blue] {message}"
        super().__init__(self.message)


class BenchDockerComposeFileNotFound(BenchException):
    def __init__(
        self,
        bench_name: str,
        path: Path,
        message: str = 'Compose file not found at {}. Aborting operation.',
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
        message: str = '{} not running.',
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
        message: str = 'Not found at {}. Aborting operation.',
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
        message: str = 'Remove dirs failed at {}.',
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
        message: str = 'Log file not found at {}.',
    ):
        self.bench_name = bench_name
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.bench_name, self.message)


class BenchWorkersStartError(BenchException):
    def __init__(
        self,
        bench_name: str,
        message: str = 'Workers not able to start.',
    ):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchWorkersSupervisorConfigurtionGenerateError(BenchException):
    def __init__(
        self,
        bench_name: str,
        message: str = 'Failed to configure workers.',
    ):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)


class BenchWorkersSupervisorConfigurtionNotFoundError(BenchException):
    def __init__(
        self,
        bench_name: str,
        config_dir: str,
        message: str = 'Superviosrd workers configuration not found in {}.',
    ):
        self.bench_name = bench_name
        self.config_dir = config_dir
        self.message = message.format(self.config_dir)
        super().__init__(self.bench_name, self.message)


class BenchConfigFileNotFound(BenchException):
    def __init__(self, bench_name, config_path, message='Config file not found at {}.'):
        self.bench_name = bench_name
        self.config_path = config_path
        self.message = message.format(config_path)
        super().__init__(self.bench_name, self.message)


class BenchConfigValidationError(BenchException):
    def __init__(self, bench_name, config_path, message='FM bench config not valid at {}'):
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
    def __init__(self, bench_name, message="Services not running."):
        self.bench_name = bench_name
        self.message = message
        super().__init__(self.bench_name, self.message)
