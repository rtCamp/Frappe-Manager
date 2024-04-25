from typing import Optional

class ComposeFileException(Exception):
    def __init__(
        self,
        error_msg: str,
        exception: Optional[Exception] = None
    ):
        error_msg = f"{error_msg}"
        if exception:
            error_msg = f"{error_msg}\nException : {exception}"
        super().__init__(error_msg)

class ComposeServiceNotFound(Exception):
    def __init__(self, service_name: str, message: str = 'Compose service not found.') -> None:
        self.msg = service_name + ' ' + message
        super().__init__(self.msg)

class ComposeSecretNotFoundError(Exception):
    """Exception raised when a Docker Compose secret value is not found."""

    def __init__(self, secret_name, compose_file_path: str, message="Docker Compose at {} secret value not found"):
        self.secret_name = secret_name
        self.compose_file_path = compose_file_path
        self.message = f"{message.format(self.compose_file_path)}: {secret_name}"
        super().__init__(self.message)
