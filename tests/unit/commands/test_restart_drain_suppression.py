"""`fm restart`: which invocations actually enter the drain gate.

Draining is the default for a worker restart -- fm suspends the workers, waits
for in-flight jobs and aborts rather than kill them. Two flags opt out of that
wait *implicitly*, without the user typing --no-drain: `--force` means
kill-fast, and `--service` is a surgical single-service restart. restart.py
turns `drain` off for either of them (`if force or service`).

test_restart_guards.py already pins the *parse-time* conflict matrix (which
flag combinations are refused). This file pins the *effect* one step later:
whether the drain gate is entered at all, and what the worker leg is told.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.restart import restart
from frappe_manager.output_manager import get_global_output_handler

runner = CliRunner()


@pytest.fixture
def cli(tmp_path):
    """Isolated app around the real restart command, with a root ctx.obj.

    The bench directory exists on disk so the parse-time sitename callback
    resolves, and the root callback supplies the `services`/`verbose` context
    the command body reads.
    """
    app = typer.Typer()

    @app.callback()
    def _root(ctx: typer.Context):
        ctx.obj = {"services": MagicMock(), "verbose": False}

    app.command("restart")(restart)

    benches = tmp_path / "sites"
    (benches / "x.localhost").mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", benches):
        yield app


@contextmanager
def wired():
    """Stub out everything past the flag guards: bench, orchestrator, gate."""
    bench = MagicMock()
    bench.running = True
    bench.compose_file_manager.get_services_list.return_value = ["frappe", "nginx", "socketio"]
    bench.workers.compose_file_manager.exists.return_value = True
    bench.workers.compose_file_manager.get_services_list.return_value = ["worker-short"]

    orchestrator = MagicMock()
    orchestrator.drain_workers.return_value = True

    with (
        patch("frappe_manager.commands.restart.check_bench_migration_required"),
        patch("frappe_manager.commands.restart.Bench") as bench_cls,
        patch(
            "frappe_manager.site_manager.modules.deploy_orchestrator.DeployOrchestrator",
            return_value=orchestrator,
        ),
    ):
        bench_cls.get_object.return_value = bench
        yield bench, orchestrator


@pytest.mark.timeout(15)
def test_plain_restart_drains_before_restarting_workers(cli):
    """Baseline: with neither --force nor --service, drain stays on."""
    with wired() as (bench, orchestrator):
        result = runner.invoke(cli, ["restart", "x.localhost"])

    assert result.exit_code == 0, result.output
    orchestrator.drain_workers.assert_called_once()
    orchestrator.resume_workers.assert_called_once()
    bench.restart_workers_containers_services.assert_called_once_with(use_container_restart=False, force=False)


@pytest.mark.timeout(15)
def test_force_alone_disables_the_drain_gate(cli):
    """--force implies no-drain even though the user never typed --no-drain."""
    handler = get_global_output_handler()

    with wired() as (bench, orchestrator), patch.object(handler, "warning") as warning:
        result = runner.invoke(cli, ["restart", "x.localhost", "--force"])

    assert result.exit_code == 0, result.output
    orchestrator.drain_workers.assert_not_called()
    orchestrator.resume_workers.assert_not_called()
    bench.restart_workers_containers_services.assert_called_once_with(use_container_restart=False, force=True)
    # The implicit opt-out is announced: in-flight jobs will be interrupted.
    assert any("WITHOUT draining" in str(call.args[0]) for call in warning.call_args_list)


@pytest.mark.timeout(15)
def test_explicit_no_drain_matches_force_on_the_gate(cli):
    """--no-drain is the explicit spelling of what --force implies."""
    with wired() as (bench, orchestrator):
        result = runner.invoke(cli, ["restart", "x.localhost", "--no-drain"])

    assert result.exit_code == 0, result.output
    orchestrator.drain_workers.assert_not_called()
    bench.restart_workers_containers_services.assert_called_once_with(use_container_restart=False, force=False)


@pytest.mark.timeout(15)
def test_service_restart_is_surgical_and_never_drains(cli):
    """--service restarts only the named service and skips the gate entirely."""
    with wired() as (bench, orchestrator):
        result = runner.invoke(cli, ["restart", "x.localhost", "--service", "socketio"])

    assert result.exit_code == 0, result.output
    orchestrator.drain_workers.assert_not_called()
    bench.restart_supervisor_service.assert_called_once_with("socketio", force=False)
    bench.restart_workers_containers_services.assert_not_called()
