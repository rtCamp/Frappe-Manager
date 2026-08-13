"""``LoggingOutputHandler.exit`` must fall back unless the delegate can really exit.

The wrapper does not require its delegate to implement ``exit``: it looks the attribute
up and forwards only when it is BOTH present and callable, otherwise it performs the
exit itself (display the error, then ``typer.Exit`` -- or ``sys.exit`` when the caller
asked for a hard exit). Handlers in this package differ here, and the JSON/silent
family is free to omit the method, so dropping either half of that guard turns a
missing-or-odd attribute into a ``TypeError`` raised from inside the error path -- the
worst possible moment, since the process is already on its way out.

Defended here: forwarding happens for a callable, and a non-callable attribute is
treated exactly like a missing one. The ERROR mirror line is written either way.
"""

from unittest.mock import Mock

import pytest
import typer

from frappe_manager.output_manager.logging_output import LoggingOutputHandler


class _Delegate:
    """Minimal OutputHandler stand-in; `exit` is set per test (or left absent)."""

    verbose = False

    def __init__(self):
        self.display_error = Mock(name="display_error")


def _handler(delegate):
    handler = LoggingOutputHandler(delegate)
    # Never touch the real ~/frappe/logs/fm.log from a unit test.
    handler.logger = Mock(name="logger")
    return handler


def test_callable_delegate_exit_is_forwarded_verbatim():
    delegate = _Delegate()
    delegate.exit = Mock(name="exit")
    handler = _handler(delegate)

    handler.exit("boom", ":x:", os_exit=True, error_msg="details")

    # Forwarded positionally, in the delegate's own parameter order.
    assert delegate.exit.call_args.args == ("boom", ":x:", True, "details")
    assert delegate.exit.call_count == 1
    # The delegate owns the exit from here; the wrapper must not double up.
    delegate.display_error.assert_not_called()


def test_missing_delegate_exit_falls_back_to_display_and_typer_exit():
    delegate = _Delegate()
    handler = _handler(delegate)

    with pytest.raises(typer.Exit):
        handler.exit("boom")

    delegate.display_error.assert_called_once_with("boom", ":no_entry:")


def test_non_callable_delegate_exit_is_treated_as_missing():
    # A truthy-but-not-callable attribute is the case the `callable()` half of the
    # guard exists for: calling it would raise TypeError out of the error path.
    delegate = _Delegate()
    delegate.exit = "shutting down"

    handler = _handler(delegate)

    with pytest.raises(typer.Exit):
        handler.exit("boom")

    delegate.display_error.assert_called_once_with("boom", ":no_entry:")


def test_fallback_honours_a_hard_exit_request():
    delegate = _Delegate()
    delegate.exit = "shutting down"
    handler = _handler(delegate)

    with pytest.raises(SystemExit) as excinfo:
        handler.exit("boom", os_exit=True)

    assert excinfo.value.code == 1


def test_exit_is_mirrored_to_the_log_before_the_fallback():
    delegate = _Delegate()
    handler = _handler(delegate)

    with pytest.raises(typer.Exit):
        handler.exit("boom", error_msg="details")

    # First ERROR line is the EXIT mirror; the fallback's display_error logs after it.
    logged = handler.logger.error.call_args_list[0].args[0]
    assert "EXIT: boom" in logged
    assert "details" in logged
