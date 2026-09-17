"""`fm stop`: in-flight RQ jobs are waited for, and the queue is never left suspended.

Measured before this existed, on a real bench: a `sleep 600` job 62 seconds in, then `fm stop`
returned in 3.2 seconds having killed it, with nothing printed about workers at all. After
restarting the bench the job was still `JobStatus.STARTED` with empty `exc_info` -- a zombie that
will never finish, never fail and never be retried, while RQ's registry still claims it is
running. A container stop grants ten seconds and then kills; that is not enough for a real job,
and RQ only treats a shutdown as "finish this job first" once the suspend has landed.

Two things separate this from the other drain call sites:

* the resume runs on the SUCCESS path too, because `rq:suspended` is a redis key and the bench's
  redis-queue persists (RDB `save` plus a `/data` volume), so a flag left set survives the stop
  and comes back with the bench -- `fm start` would then bring up workers that process nothing;
* a drain timeout aborts and stops NOTHING, like every other drain call site. The workers are
  resumed first, so the bench is left exactly as it was found -- up and processing -- which is
  what makes refusing safe: `--no-drain` is the way through, and stopping anyway would have made
  this the one command where a refusal still did the thing.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.stop import stop
from frappe_manager.site_manager.modules.deploy_orchestrator import DrainUnavailable

runner = CliRunner()


@pytest.fixture
def cli(tmp_path):
    app = typer.Typer()

    @app.callback()
    def _root(ctx: typer.Context):
        ctx.obj = {"services": MagicMock(), "verbose": False}

    app.command("stop")(stop)

    benches = tmp_path / "sites"
    (benches / "x.localhost").mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", benches):
        yield app


@contextmanager
def wired():
    bench = MagicMock()
    bench.name = "x.localhost"
    bench.bench_config.workers = None

    orchestrator = MagicMock()
    orchestrator.drain_workers.return_value = True
    orchestrator.workers_config.drain_timeout = 300

    with (
        patch("frappe_manager.commands.stop.Bench") as bench_cls,
        patch("frappe_manager.commands.stop.DeployOrchestrator", return_value=orchestrator),
    ):
        bench_cls.get_object.return_value = bench
        yield bench, orchestrator


@pytest.mark.timeout(15)
def test_it_drains_before_stopping_the_containers(cli):
    with wired() as (bench, orchestrator):
        result = runner.invoke(cli, ["stop", "x.localhost"])

    assert result.exit_code == 0, result.output
    orchestrator.drain_workers.assert_called_once_with()
    bench.stop.assert_called_once_with()


@pytest.mark.timeout(15)
def test_the_queue_is_never_left_suspended(cli):
    """The flag outlives the containers: redis-queue persists, so a set flag comes back with the
    bench and `fm start` would bring up workers that quietly process nothing."""
    with wired() as (_bench, orchestrator):
        result = runner.invoke(cli, ["stop", "x.localhost"])

    assert result.exit_code == 0, result.output
    orchestrator.resume_workers.assert_called_once_with()


@pytest.mark.timeout(15)
def test_a_timeout_stops_nothing_and_leaves_the_bench_as_it_was(cli):
    """Nothing stopped, workers resumed: the refusal is a true no-op, so a retry is safe and
    `--no-drain` is the documented way through."""
    with wired() as (bench, orchestrator):
        orchestrator.drain_workers.return_value = False
        result = runner.invoke(cli, ["stop", "x.localhost"])

    assert result.exit_code == 1
    bench.stop.assert_not_called()
    orchestrator.resume_workers.assert_called_once_with()


@pytest.mark.timeout(15)
def test_no_drain_skips_the_wait_and_stops_immediately(cli):
    with wired() as (bench, orchestrator):
        result = runner.invoke(cli, ["stop", "x.localhost", "--no-drain"])

    assert result.exit_code == 0, result.output
    orchestrator.drain_workers.assert_not_called()
    orchestrator.resume_workers.assert_not_called()
    bench.stop.assert_called_once_with()


@pytest.mark.timeout(15)
def test_an_image_that_cannot_drain_still_stops(cli):
    """DrainUnavailable is not a timeout: an image predating fmx can never be drained, and a stop
    must not become impossible because of it."""
    with wired() as (bench, orchestrator):
        orchestrator.drain_workers.side_effect = DrainUnavailable("no fmx in this image.")
        result = runner.invoke(cli, ["stop", "x.localhost"])

    assert result.exit_code == 0, result.output
    bench.stop.assert_called_once_with()
    orchestrator.resume_workers.assert_not_called()
