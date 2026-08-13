"""Where a `BenchOrchestrator`'s progress output goes.

The orchestrator narrates the whole create -- `change_head` per phase, `print` per decision, and
the external-database gate's prompts. Every one of those goes through `self.output`, and the
constructor is the only place that is decided: the handler the caller injected, or a Rich one it
builds for itself.

Both halves matter. `fm` passes its own handler down (a non-interactive run, a captured log, the
test harnesses), and a handler that is silently ignored takes the operator's prompts with it. An
orchestrator constructed without one is what every plain `Bench` does, and it must end up with a
working handler rather than something that raises at the first `change_head`.
"""

from unittest.mock import MagicMock

from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.modules.bench_orchestrator import BenchOrchestrator


def test_an_injected_handler_is_the_one_that_gets_written_to():
    handler = MagicMock()
    orchestrator = BenchOrchestrator(MagicMock(), output_handler=handler)

    assert orchestrator.output is handler

    # …and it really is the sink the phases use, not just an attribute that was stored.
    orchestrator._skip_phase6_for_attach()  # noqa: SLF001

    assert handler.change_head.called
    assert handler.print.called


def test_without_a_handler_the_orchestrator_builds_a_working_one():
    orchestrator = BenchOrchestrator(MagicMock())

    assert isinstance(orchestrator.output, RichOutputHandler)
