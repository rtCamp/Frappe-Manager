from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.bench_config import WorkersConfig
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployOrchestrator
from frappe_manager.site_manager.modules.worker_drain import rq_suspended
from frappe_manager.site_manager.site import Bench


@example(
    "Stop a bench",
    "{benchname}",
    detail="Waits for in-flight RQ jobs first.",
    benchname="mybench",
)
@example(
    "Stop now, interrupting running jobs",
    "{benchname} --no-drain",
    benchname="mybench",
)
def stop(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    drain: Annotated[
        bool,
        typer.Option(
            "--drain/--no-drain",
            help="Wait for in-flight RQ jobs before stopping the workers, and abort the stop if they outlast \\[workers].drain_timeout; --no-drain interrupts them instead.",
            show_default=True,
        ),
    ] = True,
):
    r"""
    Stop a bench's containers, admin tools and workers.

    In-flight background jobs are waited for first: a container stop gives a running job ten seconds and then kills it, which leaves the job recorded as still running with nothing to finish it. If they outlast \[workers].drain_timeout the bench is left running and nothing is stopped, so the stop can be retried; --no-drain stops immediately and interrupts them.

    Nothing is deleted; fm start brings the bench back.
    """
    # No migration gate here: "stop" is in app_callback's commands_skip_bench_migration
    # whitelist (with "delete"), i.e. stopping an unmigrated bench must always work.

    services_manager = ctx.obj["services"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    orchestrator = DeployOrchestrator(bench, output_handler=output)
    if drain:
        # A timeout aborts, same as every other drain call site: the workers are resumed first, so
        # the bench is left exactly as it was found -- up, processing -- and the stop can be
        # retried or forced with --no-drain. Stopping anyway would have been the one place where a
        # refused command still did the thing.
        #
        # The window is deliberately EMPTY. Every other caller does its work inside the suspend;
        # here the drain buys the in-flight jobs their finish and the flag must be cleared BEFORE
        # the containers go down, because `rq:suspended` is a redis key and the bench's
        # redis-queue persists it (RDB `save` on a `/data` volume): a flag left set survives the
        # stop and comes back with the bench, so `fm start` would bring up workers that quietly
        # process nothing. Wrapping it still earns the signal safety, and the drain wait is the
        # long part where a Ctrl-C or a dropped SSH actually lands.
        with rq_suspended(orchestrator, output, action="stop"):
            pass
    else:
        kill_timeout = (bench.bench_config.workers or WorkersConfig()).kill_timeout
        output.warning(
            f"Stopping WITHOUT draining: in-flight jobs are interrupted "
            f"(container stop grace is 10s, force-stop after {kill_timeout}s)",
        )

    with spinner(output, f"Stopping {bench.name}"):
        bench.stop()
