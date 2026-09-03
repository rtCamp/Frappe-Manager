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
        detail: str | None = None,
    ):
        if domain:
            self.message = f"Certificate generation failed for {domain}."
        else:
            self.message = "Certificate generation failed."
        if detail:
            self.message = f"{self.message} {detail}"
        self.domain = domain
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


class SSLCertificateManualRenewalRequired(FrappeManagerException):
    """Raised for a certificate with no automated renewal path: fm cannot mint new bytes for it.

    A custom certificate (`fm ssl add --custom`) is the one case today. fm never stores the
    original --cert/--key/--ca paths (see CustomCertificate), so there is nothing to re-copy from,
    and there is no ACME account to re-issue against either -- the only correct action is telling
    the operator to get a fresh certificate and re-run the add.
    """

    def __init__(self, domain: str, message: str | None = None):
        self.domain = domain
        self.message = message or (
            f"'{domain}' has a custom certificate; fm does not store the original --cert/--key/--ca "
            f"files, only their bytes, so it cannot renew this automatically. Get a fresh "
            f"certificate and run 'fm ssl add <bench>/{domain} --custom --cert PATH --key PATH' "
            f"again to rotate it."
        )
        super().__init__(self.message)
