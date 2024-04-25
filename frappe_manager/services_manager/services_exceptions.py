
class ServicesComposeNotExist(Exception):
    def __init__(self, message):
        message = message
        super().__init__(message)

class ServicesSecretsDBRootPassNotExist(Exception):
    def __init__(self, message):
        message = message
        super().__init__(message)

class ServicesDBNotStart(Exception):
    def __init__(self, message):
        message = message
        super().__init__(message)

class ServicesException(Exception):
    def __init__(self, message):
        message = message
        super().__init__(message)

class ServicesNotCreated(ServicesException):
    def __init__(self, message: str = 'Not able to generate services compose file.'):
        message = message
        super().__init__(message)

class DatabaseServiceException(Exception):
    def __init__(self, service_name:str , message:str):
        self.message: str = f'Service {service_name} db server : {message}'
        super().__init__(self.message)

class DatabaseServicePasswordNotFound(DatabaseServiceException):
    def __init__(self, service_name: str, message = 'Failed to determine root password.') -> None:
        self.service_name = service_name
        self.message = message.format(self.service_name)
        super().__init__(self.service_name,self.message)

class DatabaseServiceUserRemoveFailError(DatabaseServiceException):
    def __init__(self,username: str, service_name: str, message = 'Failed to remove user {}.') -> None:
        self.service_name = service_name
        self.username = username
        self.message = message.format(self.username)
        super().__init__(self.service_name,self.message)

class DatabaseServiceDBRemoveFailError(DatabaseServiceException):
    def __init__(self,db_name: str, service_name: str, message = 'Failed to remove db {}.') -> None:
        self.service_name = service_name
        self.db_name = db_name
        self.message = message.format(db_name)
        super().__init__(self.service_name,self.message)

class DatabaseServiceDBNotFoundError(DatabaseServiceException):
    def __init__(self, db_name: str, service_name: str, message = 'DB not found {}.') -> None:
        self.service_name = service_name
        self.db_name = db_name
        self.message = message.format(db_name)
        super().__init__(self.service_name,self.message)

class DatabaseServiceStartTimeout(DatabaseServiceException):
    def __init__(self,timeout: int, service_name: str, message = 'DB failed to start, waited for {}s.') -> None:
        self.service_name = service_name
        self.message = message.format(timeout)
        super().__init__(self.service_name,self.message)

class DatabaseServiceDBExportFailed(DatabaseServiceException):
    def __init__(self,service_name: str, db_name:str, message = 'DB export failed for db name {}.') -> None:
        self.service_name = service_name
        self.message = message.format(db_name)
        super().__init__(self.service_name,self.message)

class DatabaseServiceDBImportFailed(DatabaseServiceException):
    def __init__(self,service_name: str, db_dump_path:str, message = 'DB import failed for db dump {}.') -> None:
        self.service_name = service_name
        self.message = message.format(db_dump_path)
        super().__init__(self.service_name,self.message)

class DatabaseServiceDBCreateFailed(DatabaseServiceException):
    def __init__(self,service_name: str, db_name:str, message = 'DB create failed for db name {}.') -> None:
        self.service_name = service_name
        self.message = message.format(db_name)
        super().__init__(self.service_name,self.message)
