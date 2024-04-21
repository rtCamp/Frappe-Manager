from datetime import datetime
from typing import Optional

from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining


class SSLCertificateNotFoundError(Exception):
    """Exception raised when a certificate is not found."""

    def __init__(self, domain, message="No ssl certificate is issued for {}."):
        self.domain = domain
        self.message = message.format(self.domain)
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
        self.message = f"Certificate generation failed."
        super().__init__(self.message)


class SSLCertificateNotDueForRenewalError(Exception):
    def __init__(
        self,
        domain,
        expiry_date: datetime,
        message='[blue]{}:[/blue] Certificate is not due for renewal will expire in {}.',
    ):
        self.domain = domain
        self.expiry_date = expiry_date
        self.time_remaining_txt = format_ssl_certificate_time_remaining(self.expiry_date)
        self.message = message.format(self.domain, self.time_remaining_txt)
        super().__init__(self.message)
