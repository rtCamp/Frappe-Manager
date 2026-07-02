class MigrationExceptionInBench(Exception):
    def __init__(
        self,
        error_msg: str,
    ):
        super().__init__(error_msg)


class BackupFailureException(Exception):
    """
    Raised when backup fails during migration.

    This exception is used to distinguish backup failures from other migration errors,
    allowing for specific user prompts and handling.
    """

    def __init__(
        self,
        error_msg: str,
        bench_name: str,
        original_exception: Exception,
    ):
        super().__init__(error_msg)
        self.bench_name = bench_name
        self.original_exception = original_exception
