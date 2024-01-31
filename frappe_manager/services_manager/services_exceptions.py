
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
    def __init__(self, message):
        message = message
        super().__init__(message)
