class ServicesComposeNotExist(Exception):
    """Exception raised when services Docker Compose file does not exist."""

    def __init__(self, message):
        message = message
        super().__init__(message)


class ServicesSecretsDBRootPassNotExist(Exception):
    """Exception raised when database root password secret does not exist."""

    def __init__(self, message):
        message = message
        super().__init__(message)


class ServicesDBNotStart(Exception):
    """Exception raised when database service fails to start."""

    def __init__(self, message):
        message = message
        super().__init__(message)


class ServicesException(Exception):
    """Base exception for all services-related errors."""

    def __init__(self, message):
        message = message
        super().__init__(message)


class ServicesNotCreated(ServicesException):
    """Exception raised when services compose file generation fails."""

    def __init__(self, message: str = 'Not able to generate services compose file.'):
        message = message
        super().__init__(message)


class DatabaseServiceException(Exception):
    """Base exception for database service operations."""

    def __init__(self, service_name: str, message: str):
        self.message: str = f'Service {service_name} db server : {message}'
        super().__init__(self.message)


class DatabaseServiceQueryAccessDenied(Exception):
    """Exception raised when database query access is denied."""

    def __init__(self, query: str, message='Access denied for query {}') -> None:
        self.query = query
        self.message = message.format(query)
        super().__init__(self.message)


class DatabaseServicePasswordNotFound(DatabaseServiceException):
    """Exception raised when database root password cannot be determined."""

    def __init__(self, service_name: str, message='Failed to determine root password.') -> None:
        self.service_name = service_name
        self.message = message.format(self.service_name)
        super().__init__(self.service_name, self.message)


class DatabaseServiceUserRemoveFailError(DatabaseServiceException):
    """Exception raised when database user removal fails."""

    def __init__(self, username: str, service_name: str, message='Failed to remove user {}.') -> None:
        self.service_name = service_name
        self.username = username
        self.message = message.format(self.username)
        super().__init__(self.service_name, self.message)


class DatabaseServiceDBRemoveFailError(DatabaseServiceException):
    """Exception raised when database removal fails."""

    def __init__(self, db_name: str, service_name: str, message='Failed to remove db {}.') -> None:
        self.service_name = service_name
        self.db_name = db_name
        self.message = message.format(db_name)
        super().__init__(self.service_name, self.message)


class DatabaseServiceDBNotFoundError(DatabaseServiceException):
    """Exception raised when database is not found."""

    def __init__(self, db_name: str, service_name: str, message='DB not found {}.') -> None:
        self.service_name = service_name
        self.db_name = db_name
        self.message = message.format(db_name)
        super().__init__(self.service_name, self.message)


class DatabaseServiceStartTimeout(DatabaseServiceException):
    """Exception raised when database fails to start within timeout period."""

    def __init__(self, timeout: int, service_name: str, message='DB failed to start, waited for {}s.') -> None:
        self.service_name = service_name
        self.message = message.format(timeout)
        super().__init__(self.service_name, self.message)


class DatabaseServiceDBExportFailed(DatabaseServiceException):
    """Exception raised when database export fails."""

    def __init__(self, service_name: str, db_name: str, message='DB export failed for db name {}.') -> None:
        self.service_name = service_name
        self.message = message.format(db_name)
        super().__init__(self.service_name, self.message)


class DatabaseServiceDBImportFailed(DatabaseServiceException):
    """Exception raised when database import fails."""

    def __init__(self, service_name: str, db_dump_path: str, message='DB import failed for db dump {}.') -> None:
        self.service_name = service_name
        self.message = message.format(db_dump_path)
        super().__init__(self.service_name, self.message)


class DatabaseServiceDBCreateFailed(DatabaseServiceException):
    """Exception raised when database creation fails."""

    def __init__(self, service_name: str, db_name: str, message='DB create failed for db name {}.') -> None:
        self.service_name = service_name
        self.message = message.format(db_name)
        super().__init__(self.service_name, self.message)
