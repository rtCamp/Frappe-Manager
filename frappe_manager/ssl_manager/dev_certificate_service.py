"""
Dev SSL certificate service using a local CA.

Generates locally-trusted certificates for development without internet access.
The local CA is installed into the host OS trust store once, then reused for all sites.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager


class DevCertificateService:
    """
    SSL certificate service using a locally-generated CA.

    Generates certificates for development with no internet access required.
    The CA is created on first use and installed into the host trust store,
    so browsers trust all subsequent dev certificates automatically.
    """

    def __init__(
        self,
        ssl_service_dir: Path,
        output_handler: OutputHandler | None = None,
    ):
        self.logger = get_logger(component="dev_ssl")
        self.root_dir = ssl_service_dir / "dev"
        self.ca_dir = self.root_dir / "ca"
        self.ca_key_path = self.ca_dir / "rootCA-key.pem"
        self.ca_cert_path = self.ca_dir / "rootCA.pem"
        self.ca_sentinel_path = self.ca_dir / ".installed"
        self.output = output_handler or RichOutputHandler()

        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.ca_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # CA management
    # ------------------------------------------------------------------

    def _load_ca(self) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        """Load existing CA key and certificate from disk."""
        key_data = self.ca_key_path.read_bytes()
        cert_data = self.ca_cert_path.read_bytes()
        ca_key = serialization.load_pem_private_key(key_data, password=None)
        ca_cert = x509.load_pem_x509_certificate(cert_data)
        self.output.debug("Loaded existing local dev CA")
        return ca_key, ca_cert  # type: ignore[return-value]

    def _generate_ca(self) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        """Generate a new local CA key and self-signed certificate."""
        ca_key = ec.generate_private_key(ec.SECP384R1())

        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "Frappe Manager Dev CA"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Frappe Manager"),
            ]
        )

        now = datetime.now(UTC)
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )

        # Persist CA key (restricted permissions) and cert
        self.ca_key_path.write_bytes(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.ca_key_path.chmod(0o600)

        self.ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        self.output.debug(f"Generated new local dev CA at {self.ca_dir}")
        return ca_key, ca_cert

    def _ensure_ca(self) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        """Load existing CA or generate a new one."""
        if self.ca_key_path.exists() and self.ca_cert_path.exists():
            return self._load_ca()

        self.output.change_head("Setting up local dev CA")
        ca_key, ca_cert = self._generate_ca()
        self.output.print("Local dev CA created")
        return ca_key, ca_cert

    def _ensure_ca_installed(self) -> None:
        """Install CA into system trust stores (one-time, guarded by sentinel)."""
        if self.ca_sentinel_path.exists():
            return

        self.output.change_head("Installing dev CA into system trust store")
        TrustStoreManager(self.output).install(self.ca_cert_path)
        self.ca_sentinel_path.touch()
        self.output.print("Dev CA installed — browsers will now trust local dev certificates")

    # ------------------------------------------------------------------
    # SSLCertificateService Protocol implementation
    # ------------------------------------------------------------------

    def generate_certificate(self, certificate: SSLCertificate, dry_run: bool = False) -> tuple[Path, Path]:
        """
        Generate a leaf certificate signed by the local dev CA.

        Args:
            certificate: Certificate configuration (uses domain field)
            dry_run: If True, generates cert but skips trust store installation

        Returns:
            Tuple of (privkey_path, fullchain_path)
        """
        self.output.change_head(f"Generating dev SSL certificate for {certificate.domain}")

        ca_key, ca_cert = self._ensure_ca()

        if not dry_run:
            self._ensure_ca_installed()
        else:
            self.output.debug("Skipping trust store installation (dry run)")

        leaf_key = ec.generate_private_key(ec.SECP256R1())

        now = datetime.now(UTC)
        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate.domain)]))
            .issuer_name(ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=397))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(certificate.domain)]),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )

        dest_dir = self.root_dir / certificate.domain
        dest_dir.mkdir(parents=True, exist_ok=True)

        key_path = dest_dir / "key.pem"
        fullchain_path = dest_dir / "fullchain.pem"

        key_path.write_bytes(
            leaf_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        # fullchain = leaf cert + CA cert (chain format required by nginx)
        fullchain_path.write_bytes(
            leaf_cert.public_bytes(serialization.Encoding.PEM) + self.ca_cert_path.read_bytes()
        )

        self.output.print(f"Dev certificate generated for {certificate.domain}")
        return key_path, fullchain_path

    def renew_certificate(self, certificate: SSLCertificate, dry_run: bool = False) -> bool:
        """
        Re-issue leaf certificate (same CA, fresh validity window).

        Returns False if the cert directory doesn't exist, which triggers the
        SSLCertificateManager fallback to generate_certificate.
        """
        dest_dir = self.root_dir / certificate.domain
        if not dest_dir.exists():
            return False

        self.output.change_head(f"Renewing dev certificate for {certificate.domain}")
        self.generate_certificate(certificate, dry_run=dry_run)
        return True

    def remove_certificate(self, certificate: SSLCertificate) -> bool:
        """Remove the leaf certificate directory for the given domain."""
        dest_dir = self.root_dir / certificate.domain
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
            self.output.debug(f"Removed dev certificate directory: {dest_dir}")

        self.output.print(f"Dev certificate removed for {certificate.domain}")
        return True
