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
