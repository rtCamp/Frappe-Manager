"""
Custom / bring-your-own SSL certificate service.

fm issues nothing here: the operator is the certificate authority. `generate_certificate`
validates the `--cert`/`--key`/`--ca` files supplied at `fm ssl add --custom` time and copies
their bytes into the same `<ssl_dir>/custom/<domain>/` layout every other certificate type uses
(see DevCertificateService), so the rest of the pipeline -- CertificateLinkManager,
VhostConfigManager, nginx reload -- needs no special-casing for this type.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

from frappe_manager import SSL_RENEW_BEFORE_DAYS
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateGenerateFailed,
    SSLCertificateManualRenewalRequired,
)
from frappe_manager.utils.helpers import format_ssl_certificate_time_remaining


def _public_key_der(key: PrivateKeyTypes) -> bytes:
    """DER-encoded SubjectPublicKeyInfo of a private key's public half."""
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _certificate_public_key_der(cert: x509.Certificate) -> bytes:
    """DER-encoded SubjectPublicKeyInfo of a certificate's public key.

    Comparing these DER bytes, rather than the key objects, is what lets an RSA key be matched
    against an RSA cert and an EC key against an EC cert with one code path.
    """
    return cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _certificate_covers_domain(cert: x509.Certificate, domain: str) -> bool:
    """Does `cert` cover `domain`, via SAN (preferred) or CN (fallback, pre-SAN convention)?

    Wildcard matching is the one level X.509 itself defines: `*.example.com` covers
    `sub.example.com` but not `example.com` itself or `a.sub.example.com`.
    """
    names: list[str] = []
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = san.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if cn_attrs:
            names = [str(cn_attrs[0].value)]

    domain = domain.lower()
    for raw_name in names:
        name = raw_name.lower()
        if name == domain:
            return True
        if name.startswith("*."):
            suffix = name[2:]
            if domain.count(".") == suffix.count(".") + 1 and domain.endswith("." + suffix):
                return True
    return False


class CustomCertificateService:
    """
    SSLCertificateService implementation for `fm ssl add --custom`.

    Unlike every other implementation, this one has no issuance backend: `generate_certificate`
    validates and copies bytes the CLI already has in hand, and `renew_certificate` always refuses
    -- see SSLCertificateManualRenewalRequired for why.
    """

    def __init__(self, ssl_service_dir: Path, output_handler: OutputHandler | None = None):
        self.logger = get_logger(component="custom_ssl")
        self.root_dir = ssl_service_dir / "custom"
        self.output = output_handler or RichOutputHandler()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # SSLCertificateService Protocol implementation
    # ------------------------------------------------------------------

    def generate_certificate(self, certificate: SSLCertificate, dry_run: bool = False) -> tuple[Path, Path]:
        """Validate the operator-supplied cert/key/ca and copy them into fm's own storage.

        `dry_run` is accepted for Protocol conformance only: `fm ssl add --custom --dry-run` is
        refused at the CLI (there is no staging server to rehearse against), so this is never
        actually invoked with `dry_run=True` in production.
        """
        cert_path = getattr(certificate, "cert_source", None)
        key_path = getattr(certificate, "key_source", None)
        ca_path = getattr(certificate, "ca_source", None)

        if not cert_path or not key_path:
            # Reachable only when this certificate is NOT the one `fm ssl add --custom` just built
            # (that one always carries both) -- e.g. a certificate re-read from bench_config.toml,
            # where these fields are never persisted. Same remedy as renewal: re-run add.
            raise SSLCertificateManualRenewalRequired(certificate.domain)

        self.output.change_head(f"Importing custom SSL certificate for {certificate.domain}")

        cert_bytes = self._read_file(cert_path, "--cert", certificate.domain)
        key_bytes = self._read_file(key_path, "--key", certificate.domain)
        ca_bytes = self._read_file(ca_path, "--ca", certificate.domain) if ca_path else None

        try:
            leaf_cert = x509.load_pem_x509_certificate(cert_bytes)
        except ValueError as e:
            raise SSLCertificateGenerateFailed(
                certificate.domain, detail=f"'{cert_path}' is not a valid PEM certificate: {e}"
            ) from e

        try:
            private_key = serialization.load_pem_private_key(key_bytes, password=None)
        except (ValueError, TypeError) as e:
            raise SSLCertificateGenerateFailed(
                certificate.domain,
                detail=(
                    f"'{key_path}' is not a valid, unencrypted PEM private key: {e}. "
                    "A password-protected key is not supported; decrypt it first."
                ),
            ) from e

        if _certificate_public_key_der(leaf_cert) != _public_key_der(private_key):
            raise SSLCertificateGenerateFailed(
                certificate.domain, detail=f"'{key_path}' does not match the public key in '{cert_path}'."
            )

        if not _certificate_covers_domain(leaf_cert, certificate.domain):
            raise SSLCertificateGenerateFailed(
                certificate.domain,
                detail=f"'{cert_path}' does not cover '{certificate.domain}' (checked SAN, then CN).",
            )

        expiry = leaf_cert.not_valid_after_utc if hasattr(leaf_cert, "not_valid_after_utc") else leaf_cert.not_valid_after
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        if expiry <= now:
            raise SSLCertificateGenerateFailed(
                certificate.domain,
                detail=f"'{cert_path}' expired {format_ssl_certificate_time_remaining(expiry)}.",
            )
        if expiry - now <= timedelta(days=SSL_RENEW_BEFORE_DAYS):
            self.output.warning(
                f"'{cert_path}' expires soon ({format_ssl_certificate_time_remaining(expiry)}); "
                "fm cannot auto-renew a custom certificate, plan to re-import a fresh one."
            )

        if ca_bytes is not None:
            try:
                x509.load_pem_x509_certificate(ca_bytes)
            except ValueError as e:
                raise SSLCertificateGenerateFailed(
                    certificate.domain, detail=f"'{ca_path}' is not a valid PEM certificate: {e}"
                ) from e

        # All validation above must pass before anything touches disk: a refused import must
        # leave no trace, not an empty <domain>/ directory an operator has to notice and clean up
        # by hand. The try/except below is belt-and-suspenders for the same guarantee against a
        # write itself failing partway (disk full, permissions) after the directory is made.
        dest_dir = self.root_dir / certificate.domain
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            key_dest = dest_dir / "key.pem"
            fullchain_dest = dest_dir / "fullchain.pem"
            key_dest.write_bytes(key_bytes)
            fullchain_dest.write_bytes(cert_bytes)
            if ca_bytes is not None:
                (dest_dir / "ca.pem").write_bytes(ca_bytes)
        except OSError as e:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise SSLCertificateGenerateFailed(
                certificate.domain, detail=f"could not write certificate files to '{dest_dir}': {e}"
            ) from e

        self.output.print(f"Custom certificate imported for {certificate.domain}")
        return key_dest, fullchain_dest

    def renew_certificate(self, certificate: SSLCertificate, dry_run: bool = False) -> bool:
        """Always refuses: fm has no bytes to renew from. See SSLCertificateManualRenewalRequired."""
        raise SSLCertificateManualRenewalRequired(certificate.domain)

    def remove_certificate(self, certificate: SSLCertificate) -> bool:
        """Remove the leaf certificate directory for the given domain."""
        dest_dir = self.root_dir / certificate.domain
        if not dest_dir.exists():
            return False
        shutil.rmtree(dest_dir)
        return True

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _read_file(self, path: Path, flag: str, domain: str) -> bytes:
        if not path.exists():
            raise SSLCertificateGenerateFailed(domain, detail=f"{flag} file not found: '{path}'")
        try:
            return path.read_bytes()
        except OSError as e:
            raise SSLCertificateGenerateFailed(
                domain, detail=f"{flag} file '{path}' is not readable: {e}"
            ) from e
