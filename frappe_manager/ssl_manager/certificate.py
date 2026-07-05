"""
SSL Certificate models with support for multi-certificate management.

Provides base SSLCertificate model and specialized CustomDomainCertificate
for CNAME delegation scenarios.
"""

from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.utils.helpers import get_certificate_expiry_date


class SSLCertificate(BaseModel):
    """
    SSL certificate configuration and state.

    Represents a single SSL certificate for a single domain, with support
    for system state tracking and computed properties.
    """

    # User configuration
    domain: str
    ssl_type: SUPPORTED_SSL_TYPES
    challenge_type: LETSENCRYPT_PREFERRED_CHALLENGE | None = None
    enabled: bool = True
    acme_client: str = "acme.sh"  # ACME client for certificate issuance (acme.sh)
    hsts: str = "off"

    # System state fields (populated after certificate issuance)
    cert_path: Path | None = None
    key_path: Path | None = None
    issued_date: datetime | None = None
    last_renewal_attempt: datetime | None = None
    status: str = "pending"  # pending, active, expiring_soon, expired, error

    # Fields to exclude when serializing to TOML
    toml_exclude: set | None = {"domain", "toml_exclude"}

    @property
    def expiry_date(self) -> datetime | None:
        """
        Compute expiry date from certificate file at runtime.

        This ensures the expiry date is always accurate and in sync with the
        actual certificate file, avoiding stale data in config.

        Returns:
            Certificate expiry datetime, or None if cert doesn't exist
        """
        if self.cert_path and self.cert_path.exists():
            try:
                return get_certificate_expiry_date(self.cert_path)
            except Exception:
                # If we can't read the cert, return None
                return None
        return None

    @property
    def needs_renewal(self) -> bool:
        """
        Check if certificate needs renewal based on expiry date.

        A certificate needs renewal if it will expire within
        SSL_RENEW_BEFORE_DAYS days.

        Returns:
            True if certificate should be renewed, False otherwise
        """
        expiry = self.expiry_date
        if not expiry:
            return False

        renewal_threshold = expiry - timedelta(days=SSL_RENEW_BEFORE_DAYS)
        now = datetime.now()

        # Handle timezone-aware dates
        if expiry.tzinfo:
            now = now.replace(tzinfo=expiry.tzinfo)

        return now >= renewal_threshold

    @property
    def days_until_expiry(self) -> int | None:
        """
        Calculate days until certificate expires.

        Returns:
            Number of days until expiry, or None if cert doesn't exist
        """
        expiry = self.expiry_date
        if not expiry:
            return None

        now = datetime.now()
        if expiry.tzinfo:
            now = now.replace(tzinfo=expiry.tzinfo)

        delta = expiry - now
        return delta.days
