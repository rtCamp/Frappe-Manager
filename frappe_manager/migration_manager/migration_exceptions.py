from frappe_manager.exceptions import FrappeManagerException


class MigrationExceptionInBench(FrappeManagerException):
    def __init__(
        self,
        error_msg: str,
    ):
        super().__init__(error_msg)
