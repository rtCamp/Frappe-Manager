"""The one drain gate, now depended on by `fm restart`, `fm stop`, `fm update` and `fm apps add`.

Written out by hand four times before this, once per command, and the wording had already started
to drift. The timeout message is the one that matters most: it tells an operator their in-flight
work was not lost and how to proceed, so it is asserted here rather than four times over.

The gate has no policy switch on purpose. Every caller treats a timeout identically -- nothing
happens, `--no-drain` is the way through -- so what the callers actually differ on is who clears
the suspend flag, which the return value expresses.
"""

from unittest.mock import MagicMock

import pytest
import typer

from frappe_manager.site_manager.modules.deploy_orchestrator import DrainUnavailable
from frappe_manager.site_manager.modules.worker_drain import drain_gate

pytestmark = pytest.mark.timeout(15)


def _orchestrator(*, drained=True, unavailable=False):
    orchestrator = MagicMock()
    orchestrator.workers_config.drain_timeout = 300
    if unavailable:
        orchestrator.drain_workers.side_effect = DrainUnavailable("no fmx in this image.")
    else:
        orchestrator.drain_workers.return_value = drained
    return orchestrator


def test_a_successful_drain_tells_the_caller_it_owes_a_resume():
    """The return value IS the contract: callers resume at different points (`fm stop` before the
    containers go down, everyone else after their work), so the gate never resumes for them."""
    orchestrator = _orchestrator(drained=True)

    assert drain_gate(orchestrator, MagicMock(), action="restart") is True
    orchestrator.resume_workers.assert_not_called()


def test_a_timeout_resumes_the_workers_before_aborting():
    """An abort that left the queue suspended would turn a refusal into a silent outage: the flag
    is a redis key and outlives the process that set it."""
    orchestrator = _orchestrator(drained=False)

    with pytest.raises(typer.Exit) as exc:
        drain_gate(orchestrator, MagicMock(), action="restart")

    assert exc.value.exit_code == 1
    orchestrator.resume_workers.assert_called_once_with()


def test_the_timeout_message_promises_nothing_changed_and_names_the_way_through():
    output = MagicMock()
    orchestrator = _orchestrator(drained=False)

    with pytest.raises(typer.Exit):
        drain_gate(orchestrator, output, action="stop")

    message = output.display_error.call_args.args[0]
    assert "Drain timed out after 300s" in message
    assert "Nothing was changed" in message
    assert "the stop can be retried" in message
    assert "--no-drain" in message


def test_an_undrainable_image_warns_and_lets_the_command_proceed():
    """Not a timeout: an image predating fmx can never be drained, and no drain_timeout fixes it,
    so refusing would make the command permanently impossible on that bench."""
    output = MagicMock()
    orchestrator = _orchestrator(unavailable=True)

    assert drain_gate(orchestrator, output, action="update") is False
    orchestrator.resume_workers.assert_not_called()
    assert "Continuing without a drain" in output.warning.call_args.args[0]


def test_nothing_to_suspend_is_not_a_resume_debt():
    """`drain_workers` returns True when drain is disabled in config or frappe is not running; the
    caller must not then resume a queue it never suspended."""
    orchestrator = _orchestrator(drained=True)

    assert drain_gate(orchestrator, MagicMock(), action="app install") is True
