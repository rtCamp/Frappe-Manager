class MigrationExceptionInBench(Exception):
    def __init__(
        self,
        error_msg: str,
    ):
        super().__init__(error_msg)
