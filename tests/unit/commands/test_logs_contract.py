"""`fm logs` reads HOST log files, not docker, so it works on a stopped bench by design.

That is the whole point of the command (see its docstring), and it is also the trap: with nothing
said, the last run's output reads as live. The audit found `fm logs` on a stopped bench printing
105 lines and exiting 0 with no indication the bench was down, while `fm restart` on the same
bench gives the clearest error in the CLI.

So the contract defended here is narrow: still show the logs, still exit 0, but say the bench is
stopped -- and say more when `--follow` is waiting on a file nothing will append to.
"""

import sys
from unittest.mock import MagicMock

import pytest
import typer

from frappe_manager.commands.logs import logs

# From `sys.modules`, not `import ... as`: `commands/__init__.py` binds the `logs` FUNCTION onto
# the package, so both the dotted string and a plain import resolve to the function, and
# monkeypatch cannot reach the module's own globals through either.
logs_module = sys.modules["frappe_manager.commands.logs"]


def _ctx():
    ctx = MagicMock(spec=typer.Context)
    ctx.obj = {"services": MagicMock(), "verbose": False}
    return ctx


@pytest.fixture
def world(monkeypatch):
    bench = MagicMock()
    bench.name = "mybench"
    bench.get_available_services.return_value = ["frappe", "nginx"]
    output = MagicMock()

    monkeypatch.setattr(logs_module, "get_global_output_handler", lambda: output)
    monkeypatch.setattr(logs_module, "check_bench_migration_required", lambda *_a, **_k: None)
    bench_cls = MagicMock()
    bench_cls.get_object.return_value = bench
    monkeypatch.setattr(logs_module, "Bench", bench_cls)
    return MagicMock(bench=bench, output=output)


def _warnings(world) -> str:
    return " ".join(str(c.args[0]) for c in world.output.warning.call_args_list)


def test_a_stopped_bench_is_named_before_its_last_log_is_shown(world):
    world.bench.running = False

    logs(_ctx(), benchname="mybench", service=None, follow=False)

    assert "is not running" in _warnings(world)
    assert "fm start mybench" in _warnings(world)
    # Still shown, and still exit 0: reading a stopped bench's log is the documented behaviour.
    world.bench.logs.assert_called_once_with(False, None)


def test_following_a_stopped_bench_says_nothing_will_arrive(world):
    """`--follow` on a stopped bench waits on a file nothing is writing to, which looks like a
    hang rather than an empty log."""
    world.bench.running = False

    logs(_ctx(), benchname="mybench", service=None, follow=True)

    assert "nothing is writing to" in _warnings(world)


def test_a_running_bench_is_not_warned_about(world):
    world.bench.running = True

    logs(_ctx(), benchname="mybench", service=None, follow=False)

    world.output.warning.assert_not_called()


def test_service_logs_come_from_docker_so_the_host_file_warning_does_not_apply(world):
    """With `--service` the logs come from the container, where docker reports its own state."""
    world.bench.running = False

    logs(_ctx(), benchname="mybench", service="nginx", follow=False)

    world.output.warning.assert_not_called()
    world.bench.logs.assert_called_once_with(False, "nginx")


def test_an_unknown_service_is_still_refused(world):
    world.bench.running = True

    with pytest.raises(typer.Exit):
        logs(_ctx(), benchname="mybench", service="nope", follow=False)

    world.bench.logs.assert_not_called()
