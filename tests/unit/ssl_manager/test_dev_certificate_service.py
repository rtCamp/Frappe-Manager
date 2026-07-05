"""Unit tests for DevCertificateService."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.dev_certificate_service import DevCertificateService


def make_service(tmp_path: Path) -> DevCertificateService:
    logger = MagicMock()
    logger.child.return_value = MagicMock()
    output = MagicMock()
    return DevCertificateService(logger=logger, ssl_service_dir=tmp_path, output_handler=output)


def make_cert(domain: str = "test.localhost") -> SSLCertificate:
    return SSLCertificate(domain=domain, ssl_type=SUPPORTED_SSL_TYPES.dev)


def load_leaf_cert(fullchain_path: Path) -> x509.Certificate:
    """Load the first (leaf) certificate from a fullchain PEM file."""
    pem_data = fullchain_path.read_bytes()
    marker = b"-----BEGIN CERTIFICATE-----"
    blocks = [marker + blk for blk in pem_data.split(marker) if blk]
    return x509.load_pem_x509_certificate(blocks[0])


@pytest.mark.unit
class TestDevCertificateServiceCA:
    def test_ca_files_created_on_first_call(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            svc.generate_certificate(make_cert())
        assert svc.ca_key_path.exists()
        assert svc.ca_cert_path.exists()

    def test_ca_key_has_restricted_permissions(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            svc.generate_certificate(make_cert())
        assert oct(svc.ca_key_path.stat().st_mode)[-3:] == "600"

    def test_ca_not_regenerated_on_second_call(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            svc.generate_certificate(make_cert("a.localhost"))
        mtime = svc.ca_cert_path.stat().st_mtime
        with patch.object(svc, "_ensure_ca_installed"):
            svc.generate_certificate(make_cert("b.localhost"))
        assert svc.ca_cert_path.stat().st_mtime == mtime


@pytest.mark.unit
class TestDevCertificateServiceLeafCert:
    def test_leaf_cert_has_correct_san(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            _, fullchain = svc.generate_certificate(make_cert("mysite.localhost"))
        cert = load_leaf_cert(fullchain)
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        assert "mysite.localhost" in san.value.get_values_for_type(x509.DNSName)

    def test_leaf_cert_basic_constraints_not_ca(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            _, fullchain = svc.generate_certificate(make_cert())
        cert = load_leaf_cert(fullchain)
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert bc.value.ca is False

    def test_leaf_cert_has_server_auth_eku(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            _, fullchain = svc.generate_certificate(make_cert())
        cert = load_leaf_cert(fullchain)
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        assert ExtendedKeyUsageOID.SERVER_AUTH in eku.value

    def test_leaf_cert_validity_max_397_days(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            _, fullchain = svc.generate_certificate(make_cert())
        cert = load_leaf_cert(fullchain)
        delta = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert delta <= timedelta(days=397)

    def test_fullchain_contains_two_pem_blocks(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            _, fullchain = svc.generate_certificate(make_cert())
        count = fullchain.read_text().count("-----BEGIN CERTIFICATE-----")
        assert count == 2

    def test_key_file_written(self, tmp_path):
        svc = make_service(tmp_path)
        with patch.object(svc, "_ensure_ca_installed"):
            key_path, _ = svc.generate_certificate(make_cert())
        assert key_path.exists()
        assert key_path.read_bytes().startswith(b"-----BEGIN")


@pytest.mark.unit
class TestDevCertificateServiceRenewal:
    def test_renew_returns_true_when_domain_dir_exists(self, tmp_path):
        svc = make_service(tmp_path)
        cert = make_cert()
        with patch.object(svc, "_ensure_ca_installed"):
            svc.generate_certificate(cert)
        with patch.object(svc, "_ensure_ca_installed"):
            result = svc.renew_certificate(cert)
        assert result is True

    def test_renew_returns_false_when_domain_dir_missing(self, tmp_path):
        svc = make_service(tmp_path)
        result = svc.renew_certificate(make_cert("nonexistent.localhost"))
        assert result is False

    def test_renew_rewrites_cert_files(self, tmp_path):
        svc = make_service(tmp_path)
        cert = make_cert()
        with patch.object(svc, "_ensure_ca_installed"):
            _, fullchain = svc.generate_certificate(cert)
        assert fullchain.exists()
        with patch.object(svc, "_ensure_ca_installed"):
            svc.renew_certificate(cert)
        assert fullchain.exists()


@pytest.mark.unit
class TestDevCertificateServiceRemove:
    def test_remove_deletes_domain_directory(self, tmp_path):
        svc = make_service(tmp_path)
        cert = make_cert()
        with patch.object(svc, "_ensure_ca_installed"):
            svc.generate_certificate(cert)
        domain_dir = svc.root_dir / cert.domain
        assert domain_dir.exists()
        result = svc.remove_certificate(cert)
        assert result is True
        assert not domain_dir.exists()

    def test_remove_returns_true_when_dir_missing(self, tmp_path):
        svc = make_service(tmp_path)
        result = svc.remove_certificate(make_cert("gone.localhost"))
        assert result is True


@pytest.mark.unit
class TestDevCertificateServiceTrustStore:
    def test_dry_run_skips_trust_store_install(self, tmp_path):
        svc = make_service(tmp_path)
        with patch("frappe_manager.ssl_manager.dev_certificate_service.TrustStoreManager") as mock_ts:
            svc.generate_certificate(make_cert(), dry_run=True)
        mock_ts.return_value.install.assert_not_called()

    def test_live_run_calls_trust_store_install(self, tmp_path):
        svc = make_service(tmp_path)
        with patch("frappe_manager.ssl_manager.dev_certificate_service.TrustStoreManager") as mock_ts:
            svc.generate_certificate(make_cert(), dry_run=False)
        mock_ts.return_value.install.assert_called_once()

    def test_sentinel_prevents_reinstall(self, tmp_path):
        svc = make_service(tmp_path)
        svc.ca_sentinel_path.touch()
        with patch("frappe_manager.ssl_manager.dev_certificate_service.TrustStoreManager") as mock_ts:
            svc.generate_certificate(make_cert(), dry_run=False)
        mock_ts.return_value.install.assert_not_called()
