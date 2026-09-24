"""Unit tests for TrustStoreManager."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.ssl_manager.dev_certificate_service import dev_ca_paths
from frappe_manager.ssl_manager.trust_store_manager import MACOS_STORE, TrustStoreEntry, TrustStoreManager


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
class TestTrustStoreManagerInstallIsBestEffort:
    """install() is the policy layer: the low-level _install_* raise, install() never does,
    because issuing a dev certificate must not depend on trusting it on this host."""

    def test_returns_true_on_success(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "linux"),
            patch.object(mgr, "_install_linux") as installer,
            patch.object(mgr, "_install_nss"),
        ):
            result = mgr.install(ca_cert)
        installer.assert_called_once()
        assert result is True

    def test_a_privilege_failure_returns_false_with_actionable_instructions_and_never_raises(self, tmp_path):
        """The headless-Linux case: no sudo -> _install_linux raises -> install() warns,
        prints the exact manual trust command AND the CA path, and returns False."""
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "linux"),
            patch.object(mgr, "_install_linux", side_effect=RuntimeError("sudo: a terminal is required")),
            patch.object(mgr, "_install_nss"),
        ):
            result = mgr.install(ca_cert)  # must not raise
        assert result is False
        mgr.output.warning.assert_called_once()
        printed = " ".join(str(c.args[0]) for c in mgr.output.print.call_args_list)
        assert str(ca_cert) in printed
        assert "update-ca-certificates" in printed

    def test_nss_still_attempted_after_a_host_store_failure(self, tmp_path):
        ca_cert = tmp_path / "ca.pem"
        ca_cert.write_text("FAKE")
        mgr = make_manager()
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "linux"),
            patch.object(mgr, "_install_linux", side_effect=RuntimeError("no sudo")),
            patch.object(mgr, "_install_nss") as nss,
        ):
            mgr.install(ca_cert)
        nss.assert_called_once_with(ca_cert)

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


@pytest.mark.unit
class TestTrustStoreManagerFindMacOS:
    """find() must report every keychain copy of the CA, not just the first, or removing one leaves another trusted."""

    def test_two_certificate_copies_yield_two_entries_with_distinct_hashes(self, tmp_path):
        keychain_dir = tmp_path / "Library" / "Keychains"
        keychain_dir.mkdir(parents=True)
        (keychain_dir / "login.keychain-db").write_text("")
        mgr = make_manager()
        stdout = (
            'keychain: "/x/login.keychain-db"\n'
            "SHA-256 hash: AAAA1111\n"
            'keychain: "/x/login.keychain-db"\n'
            "SHA-256 hash: BBBB2222\n"
        )
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "darwin"),
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", return_value=None),
            patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
            entries = mgr.find()

        macos_entries = [e for e in entries if e.store == MACOS_STORE]
        assert [e.key for e in macos_entries] == ["AAAA1111", "BBBB2222"]


@pytest.mark.unit
class TestTrustStoreManagerFindLinux:
    """find() must report an anchor in every Linux CA store holding the file, not just the one install() would pick."""

    def test_reports_every_store_with_an_existing_anchor_file(self, tmp_path):
        debian_dest = tmp_path / "debian" / "fm-dev-ca.crt"
        rhel_dest = tmp_path / "rhel" / "fm-dev-ca.crt"
        debian_dest.parent.mkdir(parents=True)
        rhel_dest.parent.mkdir(parents=True)
        debian_dest.write_text("CA")
        rhel_dest.write_text("CA")
        fake_stores = (
            (debian_dest, "Debian/Ubuntu CA store", ["update-ca-certificates"]),
            (rhel_dest, "RHEL/Fedora CA store", ["update-ca-trust", "extract"]),
        )
        mgr = make_manager()
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "linux"),
            patch("frappe_manager.ssl_manager.trust_store_manager.LINUX_CA_STORES", fake_stores),
            patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", return_value=None),
        ):
            entries = mgr.find()

        stores_found = {e.store for e in entries}
        assert stores_found == {"Debian/Ubuntu CA store", "RHEL/Fedora CA store"}


@pytest.mark.unit
class TestTrustStoreManagerFindIgnoresSentinel:
    """find() asks the store itself, never the install sentinel, so a CA left trusted by an older
    or since-wiped fm install is still reported and can be removed."""

    def test_reports_a_trusted_store_even_when_no_installed_sentinel_exists_anywhere(self, tmp_path):
        ca_paths = dev_ca_paths(tmp_path / "services" / "nginx-proxy" / "ssl")
        assert not ca_paths.sentinel.exists()

        home_dir = tmp_path / "home"
        keychain_dir = home_dir / "Library" / "Keychains"
        keychain_dir.mkdir(parents=True)
        (keychain_dir / "login.keychain-db").write_text("")
        mgr = make_manager()
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "darwin"),
            patch("pathlib.Path.home", return_value=home_dir),
            patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", return_value=None),
            patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="SHA-256 hash: AAAA1111\n", stderr="")
            entries = mgr.find()

        assert not ca_paths.sentinel.exists()
        assert [e.key for e in entries] == ["AAAA1111"]


@pytest.mark.unit
class TestTrustStoreManagerUninstall:
    """uninstall() is best-effort per store: one store failing must not stop or hide removal of the others."""

    def test_continues_past_a_failing_store_and_reports_it_while_others_are_removed(self):
        mgr = make_manager()
        bad_entry = TrustStoreEntry(store="macOS login keychain", location="kc", key="badkey")
        ok_entry = TrustStoreEntry(store="NSS database (Firefox/Chrome)", location="db", key="dbkey")
        with (
            patch.object(mgr, "find", return_value=[bad_entry, ok_entry]),
            patch.object(mgr, "_remove_entry", side_effect=[RuntimeError("keychain locked"), None]),
        ):
            removed, failures = mgr.uninstall()

        assert removed == [ok_entry]
        assert len(failures) == 1
        assert "macOS login keychain" in failures[0] and "keychain locked" in failures[0]


@pytest.mark.unit
class TestTrustStoreManagerRemoveLinuxFile:
    """A failing refresh command after the anchor file is deleted must still surface as a failure:
    the OS bundle still trusts the CA."""

    def test_refresh_command_failure_raises_even_though_file_removal_succeeded(self, tmp_path):
        dest = tmp_path / "fm-dev-ca.crt"
        fake_stores = ((dest, "Debian/Ubuntu CA store", ["update-ca-certificates"]),)
        mgr = make_manager()
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.LINUX_CA_STORES", fake_stores),
            patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=""),
                MagicMock(returncode=1, stderr="update-ca-certificates: permission denied"),
            ]
            with pytest.raises(RuntimeError, match="update-ca-certificates failed"):
                mgr._remove_linux_file(dest)


@pytest.mark.unit
class TestTrustStoreManagerRemoveMacOS:
    """Removal must delete by digest with the trust-settings flag, never by common name, or it can
    take the wrong certificate or leave a dangling trust entry behind."""

    def test_deletes_by_digest_with_trust_settings_flag_not_common_name(self):
        mgr = make_manager()
        with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            mgr._remove_macos("DEADBEEF")

        args = mock_run.call_args[0][0]
        assert "-Z" in args and args[args.index("-Z") + 1] == "DEADBEEF"
        assert "-c" not in args


@pytest.mark.unit
class TestTrustStoreManagerNSSProbe:
    """A database without the CA must never be reported as trusting it, or uninstall would try to
    remove from a store that never had it."""

    def test_only_databases_where_certutil_exits_zero_are_reported(self):
        mgr = make_manager()
        has_ca_db = Path("/home/user/.pki/nssdb")
        missing_ca_db = Path("/home/user/.mozilla/firefox/xyz.default")
        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.shutil.which", return_value="/usr/bin/certutil"),
            patch.object(mgr, "_nss_databases", return_value=[has_ca_db, missing_ca_db]),
            patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=""),
                MagicMock(returncode=1, stderr="not found"),
            ]
            found = mgr._nss_databases_with_ca()

        assert found == [has_ca_db]
