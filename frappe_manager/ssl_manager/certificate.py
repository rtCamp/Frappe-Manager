"""
SSL Certificate models with support for multi-certificate management.

Provides base SSLCertificate model and specialized CustomDomainCertificate
for CNAME delegation scenarios.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
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
    challenge_type: LETSENCRYPT_PREFERRED_CHALLENGE = LETSENCRYPT_PREFERRED_CHALLENGE.http01
    enabled: bool = True
    acme_client: str = "acme.sh"  # "certbot" or "acme.sh" (default: acme.sh)
    hsts: str = "off"

    # System state fields (populated after certificate issuance)
    cert_path: Optional[Path] = None
    key_path: Optional[Path] = None
    issued_date: Optional[datetime] = None
    last_renewal_attempt: Optional[datetime] = None
    status: str = "pending"  # pending, active, expiring_soon, expired, error

    # Fields to exclude when serializing to TOML
    toml_exclude: Optional[set] = {"domain", "toml_exclude"}

    @property
    def expiry_date(self) -> Optional[datetime]:
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
    def days_until_expiry(self) -> Optional[int]:
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
