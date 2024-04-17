from typing import Optional

class SSLCertificateNotFoundError(Exception):
    """Exception raised when a certificate is not found."""

    def __init__(self, domain, message="Certificate not found for domain: "):
        self.domain = domain
        self.message = message + domain
        super().__init__(self.message)

class SSLDNSChallengeNotImplemented(Exception):
    """Exception raised for dns method not implemented."""

    def __init__(self):
        super().__init__()

class SSLCertificateChallengeFailed(Exception):
    """Exception raised when a certificate generation failed."""

    def __init__(
            self,
            challenge: str,
        ):

        self.challenge = challenge
        msg = f'{self.challenge} challenge failed.'
        super().__init__(msg)

class SSLCertificateGenerateFailed(Exception):
    """Exception raised when a certificate generation failed."""

    def __init__(
            self,
        ):
        message="Certificate generation failed."
        super().__init__(message)
