from typing import List, Optional

class SiteException(Exception):
    def __init__(
        self,
        site,
        error_msg: str,
        exception: Optional[Exception] = None
    ):
        error_msg = f"{site.name}: {error_msg}"
        super().__init__(error_msg)

class SiteWorkerNotStart(Exception):
    def __init__(
        self,
        error_msg: str,
    ):
        error_msg = f"{error_msg}"
        super().__init__(error_msg)

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
