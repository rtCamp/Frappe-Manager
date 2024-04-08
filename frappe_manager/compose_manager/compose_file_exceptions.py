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
