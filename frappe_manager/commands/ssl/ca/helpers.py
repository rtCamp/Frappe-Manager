"""Shared lookups for the `fm ssl ca` commands."""

from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from frappe_manager import CLI_SERVICES_DIRECTORY
from frappe_manager.ssl_manager.dev_certificate_service import DevCAPaths, dev_ca_paths


def ssl_service_dir() -> Path:
    return CLI_SERVICES_DIRECTORY / "nginx-proxy" / "ssl"


def ca_paths() -> DevCAPaths:
    return dev_ca_paths(ssl_service_dir())


def read_ca(cert_path: Path) -> x509.Certificate | None:
    """The CA certificate, or None when it is absent or unreadable.

    Unreadable is not an error here: every `fm ssl ca` command has something useful to say about
    a host whose CA file is corrupt, and status in particular must never be the command that
    raises instead of reporting.
    """
    if not cert_path.exists():
        return None
    try:
        return x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (ValueError, OSError):
        return None


def fingerprint(cert: x509.Certificate) -> str:
    return cert.fingerprint(hashes.SHA256()).hex().upper()


def expiry_note(cert: x509.Certificate) -> str:
    not_after = cert.not_valid_after_utc
    days = (not_after - datetime.now(UTC)).days
    stamp = not_after.strftime("%Y-%m-%d")
    return f"{stamp} (expired)" if days < 0 else f"{stamp} ({days} days left)"
