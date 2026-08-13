"""TrustStoreManager's output handler is injected-or-defaulted, never absent.

``self.output = output_handler or RichOutputHandler()`` gives the class two guarantees that every
other method leans on unconditionally (`self.output.debug(...)`, `self.output.warning(...)`):

* a caller-supplied handler is the one that is actually used — the CLI passes the handler that owns
  the live progress display, and quietly swapping in a second one would scribble over it;
* omitting the handler still yields a working manager rather than ``None``, so
  ``TrustStoreManager().install(...)`` cannot die with AttributeError.

The unsupported-platform branch is the cheapest observable place to see which handler received the
messages: it only talks to ``self.output`` and never shells out.
"""

from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.ssl_manager.trust_store_manager import TrustStoreManager


@pytest.fixture
def ca_cert(tmp_path):
    cert = tmp_path / "ca.pem"
    cert.write_text("FAKE CA")
    return cert


@pytest.mark.unit
class TestTrustStoreManagerOutputHandler:
    def test_injected_handler_receives_the_messages(self, ca_cert):
        handler = MagicMock()
        mgr = TrustStoreManager(output_handler=handler)

        assert mgr.output is handler

        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "win32"),
            patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run,
        ):
            mgr.install(ca_cert)

        mock_run.assert_not_called()
        handler.warning.assert_called_once()
        assert "win32" in handler.warning.call_args[0][0]
        handler.print.assert_called_once()
        assert str(ca_cert) in handler.print.call_args[0][0]

    def test_positional_handler_is_also_honoured(self, ca_cert):
        handler = MagicMock()

        mgr = TrustStoreManager(handler)

        assert mgr.output is handler

    def test_default_handler_is_a_usable_output_handler(self, ca_cert):
        mgr = TrustStoreManager()

        assert isinstance(mgr.output, RichOutputHandler)

        with (
            patch("frappe_manager.ssl_manager.trust_store_manager.sys.platform", "win32"),
            patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run,
        ):
            # Must not raise: the default handler has to be a real handler, not None.
            mgr.install(ca_cert)

        mock_run.assert_not_called()

    def test_explicit_none_falls_back_to_the_default_handler(self, ca_cert):
        mgr = TrustStoreManager(output_handler=None)

        assert isinstance(mgr.output, RichOutputHandler)

    def test_injected_handler_is_used_by_the_platform_install_path(self, ca_cert):
        """Not just the warning branch: the real install path reports through the same handler."""
        handler = MagicMock()
        mgr = TrustStoreManager(output_handler=handler)

        with patch("frappe_manager.ssl_manager.trust_store_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            mgr._install_macos(ca_cert)

        assert handler.debug.call_count >= 1
        assert any("keychain" in call.args[0] for call in handler.debug.call_args_list)
