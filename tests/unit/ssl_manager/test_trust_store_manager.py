"""Unit tests for TrustStoreManager."""

from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager


def make_manager() -> TrustStoreManager:
    return TrustStoreManager(output_handler=MagicMock())


@pytest.mark.unit
class TestTrustStoreManagerMacOS:
    def test_install_calls_security_add_trusted_cert(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()

        with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            mgr._install_macos(ca_cert)

        args = mock_run.call_args[0][0]
        assert "security" in args
        assert "add-trusted-cert" in args
        assert "trustRoot" in args
        assert str(ca_cert) in args

    def test_raises_on_keychain_denied(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()

        with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=36, stderr="User denied")
            with pytest.raises(RuntimeError, match="macOS denied"):
                mgr._install_macos(ca_cert)

    def test_raises_on_other_failure(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()

        with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="some error")
            with pytest.raises(RuntimeError, match="Failed to install CA"):
                mgr._install_macos(ca_cert)


@pytest.mark.unit
class TestTrustStoreManagerLinux:
    def test_debian_calls_update_ca_certificates(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()

        def which_side(cmd):
            return "/usr/bin/update-ca-certificates" if cmd == "update-ca-certificates" else None

        with patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", side_effect=which_side):
            with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr="")
                mgr._install_linux(ca_cert)

        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert any("update-ca-certificates" in cmd for cmd in cmds)

    def test_rhel_calls_update_ca_trust(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()

        def which_side(cmd):
            if cmd == "update-ca-trust":
                return "/usr/bin/update-ca-trust"
            return None

        with patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", side_effect=which_side):
            with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr="")
                mgr._install_linux(ca_cert)

        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert any("update-ca-trust" in cmd for cmd in cmds)

    def test_raises_when_no_tool_found(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()

        with patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="No supported CA trust update tool"):
                mgr._install_linux(ca_cert)


@pytest.mark.unit
class TestTrustStoreManagerNSS:
    def test_skipped_when_certutil_not_found(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()

        with patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", return_value=None):
            with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
                mgr._install_nss(ca_cert)

        mock_run.assert_not_called()
