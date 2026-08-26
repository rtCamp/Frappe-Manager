from datetime import datetime

from frappe_manager import CLI_FM_CONFIG_PATH
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining


class SSLCertificateNotFoundError(FrappeManagerException):
    """Exception raised when a certificate is not found."""

    def __init__(self, domain, message="No ssl certificate is issued for {}."):
        self.domain = domain
        self.message = message.format(self.domain)
        super().__init__(self.message)


class SSLDNSChallengeCredentailsNotFound(FrappeManagerException):
    """Exception raised for dns method required credential not found."""

    def __init__(self, message: str = f"Cloudflare dns credentials not found in {CLI_FM_CONFIG_PATH}"):
        self.message = message
        super().__init__(message)


class SSLDNSProviderNotConfigured(FrappeManagerException):
    """Exception raised when a certificate names a dns provider label that is not configured."""

    def __init__(self, label: str, available_labels: list[str]):
        self.label = label
        self.available_labels = available_labels
        if available_labels:
            self.message = (
                f"DNS provider '{label}' is not configured. Configured labels: {', '.join(available_labels)}."
            )
        else:
            self.message = f"DNS provider '{label}' is not configured. No dns provider labels are configured."
        super().__init__(self.message)


class SSLCertificateChallengeFailed(FrappeManagerException):
    """Exception raised when a certificate generation failed."""

    def __init__(
        self,
        challenge: str,
    ):
        self.challenge = challenge
        msg = f"{self.challenge} challenge failed."
        super().__init__(msg)


class SSLCertificateGenerateFailed(FrappeManagerException):
    """Exception raised when a certificate generation failed."""

    def __init__(
        self,
        domain: str | None = None,
    ):
        if domain:
            self.message = f"Certificate generation failed for {domain}."
        else:
            self.message = "Certificate generation failed."
        super().__init__(self.message)


class SSLCertificateNotDueForRenewalError(FrappeManagerException):
    """Exception raised when attempting to renew a certificate that is not due for renewal."""

    def __init__(
        self,
        domain,
        expiry_date: datetime,
        message="[fm.info]{}:[/fm.info] Certificate is not due for renewal will expire in {}.",
    ):
        self.domain = domain
        self.expiry_date = expiry_date
        self.time_remaining_txt = format_ssl_certificate_time_remaining(self.expiry_date)
        self.message = message.format(self.domain, self.time_remaining_txt)
        super().__init__(self.message)
