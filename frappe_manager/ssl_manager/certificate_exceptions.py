from typing import Optional


class CertificateNotFoundError(Exception):
    """Exception raised when a certificate is not found."""

    def __init__(self, domain, message="Certificate not found for domain: "):
        self.domain = domain
        self.message = message + domain
        super().__init__(self.message)

class DNSMethodNotImplemented(Exception):
    """Exception raised for dns method not implemented."""

    def __init__(self):
        super().__init__()

class CertificateGenerateFailed(Exception):
    """Exception raised when a certificate generation failed."""

    def __init__(self, domain, message="Certificate generate failed for domain: ", exception: Optional[Exception] = None):
        self.domain = domain
        self.message = message + domain
        super().__init__(self.message)
        if exception:
            raise exception
