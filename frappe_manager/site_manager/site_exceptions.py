from typing import List, Optional
from pathlib import Path

class BenchException(Exception):
    def __init__(self, benchname: str, message: str,):
        self.message = f"{benchname}: {message}"
        super().__init__(self.message)

class BenchDockerComposeFileNotFound(BenchException):
    def __init__(self, benchname: str, path: Path, message: str = 'Compose file not found at {}. Aborting operation.',):
        self.benchname = benchname
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.benchname,self.message)

class BenchNotFoundError(BenchException):
    def __init__(self, benchname: str, path: Path, message: str = 'Not found at {}. Aborting operation.',):
        self.benchname = benchname
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.benchname,self.message)


class BenchRemoveDirectoryError(BenchException):
    def __init__(self, benchname: str, path: Path, message: str = 'Remove dirs failed at {}.',):
        self.benchname = benchname
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.benchname,self.message)

class BenchLogFileNotFoundError(BenchException):
    def __init__(self, benchname: str, path: Path, message: str = 'Log file not found at {}.',):
        self.benchname = benchname
        self.path = path
        self.message = message.format(self.path)
        super().__init__(self.benchname,self.message)

class BenchWorkersStartError(BenchException):
    def __init__(self, benchname: str, message: str = 'Workers not able to start.',):
        self.benchname = benchname
        self.message = message
        super().__init__(self.benchname,self.message)

class BenchWorkersSupervisorConfigurtionGenerateError(BenchException):
    def __init__(self, benchname: str, message: str = 'Failed to configure workers.',):
        self.benchname = benchname
        self.message = message
        super().__init__(self.benchname,self.message)


class SiteDatabaseAddUserException(Exception):
    def __init__(
        self,
        site_name,
        error_msg: str,
    ):
        error_msg = f"{site_name}: {error_msg}"
        super().__init__(error_msg)


class SiteDatabaseStartTimeout(Exception):
    def __init__(
        self,
        site_name,
        error_msg: str,
    ):
        error_msg = f"{site_name}: {error_msg}"
        super().__init__(error_msg)

class SiteDatabaseExport(Exception):
    def __init__(
        self,
        site_name,
        error_msg: str,
    ):
        error_msg = f"{site_name}: {error_msg}"
        super().__init__(error_msg)

class BenchConfigFileNotFound(Exception):
    def __init__(
        self,
        site_name,
        path,
    ):
        error_msg = f"{site_name}: Bench config file not found at {path}"
        super().__init__(error_msg)

class BenchConfigValidationError(Exception):
    def __init__(
        self,
        site_name,
        path,
    ):
        error_msg = f"{site_name}: Bench config not valid at {path}"
        super().__init__(error_msg)
