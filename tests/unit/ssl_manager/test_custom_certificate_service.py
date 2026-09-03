"""Unit tests for CustomCertificateService (`fm ssl add --custom`).

fm issues nothing for this variant, so what matters here is entirely different from the other
services: validation of operator-supplied bytes (file presence, key/cert match, domain coverage,
expiry) and the copy-in-verbatim behaviour, rather than any issuance flow.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from frappe_manager.ssl_manager.certificate import CustomCertificate
from frappe_manager.ssl_manager.certificate_exceptions import (
    SSLCertificateGenerateFailed,
    SSLCertificateManualRenewalRequired,
)
from frappe_manager.ssl_manager.custom_certificate_service import CustomCertificateService


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _self_signed_cert(
    key: ec.EllipticCurvePrivateKey,
    *,
    common_name: str = "example.com",
    san: tuple[str, ...] | None = ("example.com",),
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> x509.Certificate:
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or now - timedelta(days=1))
        .not_valid_after(not_after or now + timedelta(days=90))
    )
    if san is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in san]), critical=False
        )
    return builder.sign(key, hashes.SHA256())


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> Path:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return path


def _write_cert(path: Path, cert: x509.Certificate) -> Path:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path


def make_service(tmp_path: Path, output=None) -> CustomCertificateService:
    return CustomCertificateService(ssl_service_dir=tmp_path / "ssl", output_handler=output or MagicMock())


def make_cert(domain: str, *, cert_source=None, key_source=None, ca_source=None) -> CustomCertificate:
    return CustomCertificate(domain=domain, cert_source=cert_source, key_source=key_source, ca_source=ca_source)


@pytest.fixture
def valid_pair(tmp_path):
    """A matching cert/key pair covering app.example.com via SAN."""
    key = _key()
    cert = _self_signed_cert(key, san=["app.example.com"])
    return _write_cert(tmp_path / "a.crt", cert), _write_key(tmp_path / "a.key", key)


@pytest.mark.unit
class TestGenerateCertificateFileValidation:
    def test_missing_cert_file_is_refused(self, tmp_path, valid_pair):
        _, key_path = valid_pair
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=tmp_path / "missing.crt", key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed, match="not found"):
            svc.generate_certificate(cert)

    def test_missing_key_file_is_refused(self, tmp_path, valid_pair):
        cert_path, _ = valid_pair
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=tmp_path / "missing.key")

        with pytest.raises(SSLCertificateGenerateFailed, match="not found"):
            svc.generate_certificate(cert)

    def test_malformed_cert_file_is_refused(self, tmp_path, valid_pair):
        _, key_path = valid_pair
        bad_cert = tmp_path / "bad.crt"
        bad_cert.write_text("not a certificate")
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=bad_cert, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed, match="not a valid PEM certificate"):
            svc.generate_certificate(cert)

    def test_malformed_key_file_is_refused(self, tmp_path, valid_pair):
        cert_path, _ = valid_pair
        bad_key = tmp_path / "bad.key"
        bad_key.write_text("not a key")
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=bad_key)

        with pytest.raises(SSLCertificateGenerateFailed, match="not a valid"):
            svc.generate_certificate(cert)

    def test_no_source_paths_refuses_manual_renewal(self, tmp_path):
        """A certificate re-read from bench_config.toml never carries cert_source/key_source."""
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com")

        with pytest.raises(SSLCertificateManualRenewalRequired):
            svc.generate_certificate(cert)


@pytest.mark.unit
class TestGenerateCertificateContentValidation:
    def test_key_not_matching_certificate_is_refused(self, tmp_path):
        cert_obj = _self_signed_cert(_key(), san=["app.example.com"])
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", _key())  # a DIFFERENT key
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed, match="does not match"):
            svc.generate_certificate(cert)

    def test_domain_not_covered_by_san_is_refused(self, tmp_path):
        key = _key()
        cert_obj = _self_signed_cert(key, san=["other.example.com"])
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed, match="does not cover"):
            svc.generate_certificate(cert)

    def test_wildcard_san_covers_a_subdomain(self, tmp_path):
        key = _key()
        cert_obj = _self_signed_cert(key, san=["*.example.com"])
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        key_dest, fullchain_dest = svc.generate_certificate(cert)

        assert key_dest.exists()
        assert fullchain_dest.exists()

    def test_wildcard_san_does_not_cover_the_bare_apex_domain(self, tmp_path):
        key = _key()
        cert_obj = _self_signed_cert(key, san=["*.example.com"])
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        svc = make_service(tmp_path)
        cert = make_cert("example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed, match="does not cover"):
            svc.generate_certificate(cert)

    def test_cn_fallback_covers_domain_when_there_is_no_san_extension(self, tmp_path):
        """Pre-SAN convention: a certificate with no SAN extension at all falls back to CN."""
        key = _key()
        cert_obj = _self_signed_cert(key, common_name="app.example.com", san=None)
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        svc.generate_certificate(cert)  # must not raise

    def test_expired_certificate_is_refused(self, tmp_path):
        key = _key()
        now = datetime.now(UTC)
        cert_obj = _self_signed_cert(
            key,
            san=["app.example.com"],
            not_before=now - timedelta(days=100),
            not_after=now - timedelta(days=1),
        )
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed, match="expired"):
            svc.generate_certificate(cert)

    def test_certificate_expiring_soon_warns_but_still_imports(self, tmp_path):
        key = _key()
        now = datetime.now(UTC)
        cert_obj = _self_signed_cert(
            key,
            san=["app.example.com"],
            not_before=now - timedelta(days=60),
            not_after=now + timedelta(days=5),
        )
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        output = MagicMock()
        svc = make_service(tmp_path, output=output)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        key_dest, fullchain_dest = svc.generate_certificate(cert)

        assert key_dest.exists()
        output.warning.assert_called_once()
        assert "expires soon" in output.warning.call_args.args[0]

    def test_malformed_ca_file_is_refused(self, tmp_path, valid_pair):
        cert_path, key_path = valid_pair
        bad_ca = tmp_path / "bad_ca.crt"
        bad_ca.write_text("not a ca")
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path, ca_source=bad_ca)

        with pytest.raises(SSLCertificateGenerateFailed, match="not a valid PEM certificate"):
            svc.generate_certificate(cert)


@pytest.mark.unit
class TestGenerateCertificateImport:
    def test_valid_import_copies_bytes_under_the_custom_domain_layout(self, tmp_path, valid_pair):
        cert_path, key_path = valid_pair
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        key_dest, fullchain_dest = svc.generate_certificate(cert)

        assert key_dest == tmp_path / "ssl" / "custom" / "app.example.com" / "key.pem"
        assert fullchain_dest == tmp_path / "ssl" / "custom" / "app.example.com" / "fullchain.pem"
        assert key_dest.read_bytes() == key_path.read_bytes()
        assert fullchain_dest.read_bytes() == cert_path.read_bytes()

    def test_valid_import_with_ca_writes_a_ca_file_alongside(self, tmp_path, valid_pair):
        cert_path, key_path = valid_pair
        ca_cert = _self_signed_cert(_key(), common_name="fm test CA", san=None)
        ca_path = _write_cert(tmp_path / "ca.crt", ca_cert)
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path, ca_source=ca_path)

        svc.generate_certificate(cert)

        ca_dest = tmp_path / "ssl" / "custom" / "app.example.com" / "ca.pem"
        assert ca_dest.read_bytes() == ca_path.read_bytes()

    def test_no_ca_given_writes_no_ca_file(self, tmp_path, valid_pair):
        cert_path, key_path = valid_pair
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        svc.generate_certificate(cert)

        assert not (tmp_path / "ssl" / "custom" / "app.example.com" / "ca.pem").exists()


@pytest.mark.unit
class TestRenewCertificate:
    def test_renew_always_refuses_with_manual_renewal_required(self, tmp_path):
        """fm has no ACME account and no stored source bytes to renew from."""
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com")

        with pytest.raises(SSLCertificateManualRenewalRequired) as exc_info:
            svc.renew_certificate(cert)

        assert "app.example.com" in exc_info.value.message
        assert "--custom" in exc_info.value.message


@pytest.mark.unit
class TestRemoveCertificate:
    def test_remove_deletes_the_domain_directory(self, tmp_path, valid_pair):
        cert_path, key_path = valid_pair
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)
        svc.generate_certificate(cert)

        result = svc.remove_certificate(cert)

        assert result is True
        assert not (tmp_path / "ssl" / "custom" / "app.example.com").exists()

    def test_remove_returns_false_when_nothing_was_ever_imported(self, tmp_path):
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com")

        assert svc.remove_certificate(cert) is False


@pytest.mark.unit
class TestNoTraceOnRefusal:
    """A rejected `--custom` add must leave no `ssl/custom/<domain>/` directory behind for the
    operator to notice and clean up by hand. Directory creation happens only after every check
    passes; each scenario below drives a DIFFERENT refusal to prove that holds for all of them,
    not just the one case a single test happened to cover.
    """

    @staticmethod
    def _domain_dir(tmp_path: Path, domain: str = "app.example.com") -> Path:
        return tmp_path / "ssl" / "custom" / domain

    def test_missing_cert_file_leaves_no_directory(self, tmp_path, valid_pair):
        _, key_path = valid_pair
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=tmp_path / "missing.crt", key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_malformed_cert_file_leaves_no_directory(self, tmp_path, valid_pair):
        _, key_path = valid_pair
        bad_cert = tmp_path / "bad.crt"
        bad_cert.write_text("not a certificate")
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=bad_cert, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_malformed_key_file_leaves_no_directory(self, tmp_path, valid_pair):
        cert_path, _ = valid_pair
        bad_key = tmp_path / "bad.key"
        bad_key.write_text("not a key")
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=bad_key)

        with pytest.raises(SSLCertificateGenerateFailed):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_key_not_matching_certificate_leaves_no_directory(self, tmp_path):
        cert_obj = _self_signed_cert(_key(), san=["app.example.com"])
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", _key())
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_domain_not_covered_leaves_no_directory(self, tmp_path):
        key = _key()
        cert_obj = _self_signed_cert(key, san=["other.example.com"])
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_expired_certificate_leaves_no_directory(self, tmp_path):
        key = _key()
        now = datetime.now(UTC)
        cert_obj = _self_signed_cert(
            key, san=["app.example.com"], not_before=now - timedelta(days=100), not_after=now - timedelta(days=1)
        )
        cert_path = _write_cert(tmp_path / "a.crt", cert_obj)
        key_path = _write_key(tmp_path / "a.key", key)
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_malformed_ca_file_leaves_no_directory(self, tmp_path, valid_pair):
        cert_path, key_path = valid_pair
        bad_ca = tmp_path / "bad_ca.crt"
        bad_ca.write_text("not a ca")
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path, ca_source=bad_ca)

        with pytest.raises(SSLCertificateGenerateFailed):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_no_source_paths_leaves_no_directory(self, tmp_path):
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com")

        with pytest.raises(SSLCertificateManualRenewalRequired):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_a_write_failure_after_the_directory_is_made_cleans_up_after_itself(self, tmp_path, valid_pair, mocker):
        """Belt-and-suspenders: even a failure AFTER every check passed (disk full, permissions
        mid-write) must not leave a partially-written directory behind."""
        cert_path, key_path = valid_pair
        mocker.patch.object(Path, "write_bytes", side_effect=OSError("disk full"))
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        with pytest.raises(SSLCertificateGenerateFailed, match="could not write"):
            svc.generate_certificate(cert)

        assert not self._domain_dir(tmp_path).exists()

    def test_valid_import_still_leaves_the_directory_present(self, tmp_path, valid_pair):
        """Sanity: the guard above must not accidentally clean up a SUCCESSFUL import too."""
        cert_path, key_path = valid_pair
        svc = make_service(tmp_path)
        cert = make_cert("app.example.com", cert_source=cert_path, key_source=key_path)

        svc.generate_certificate(cert)

        assert self._domain_dir(tmp_path).exists()
