"""Disable an APM provider on a bench."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import TelemetryProviderEnum
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.site import Bench

from ._helpers import apply_newrelic, describe_newrelic


@example(
    "Stop reporting to NewRelic",
    "{benchname} newrelic",
    detail="The stored license key and your config/newrelic.ini are kept, so enabling again is one command.",
    benchname="mybench",
)
def disable(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    provider: Annotated[
        TelemetryProviderEnum,
        typer.Argument(
            metavar="PROVIDER",
            help="APM backend to disable.",
            show_default=False,
        ),
    ] = TelemetryProviderEnum.newrelic,
):
    """
    Turn off APM reporting for a bench.

    The web process is recreated without the agent, and the provider's license key is removed from the compose file. Nothing on disk is deleted: the recorded key stays in bench_config.toml and the agent's config file keeps your tuning, so re-enabling needs no arguments. Sweep an orphaned agent config with fm prune.
    """

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    check_bench_migration_required(benchname)

    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    enabled, _ = describe_newrelic(bench)
    if not enabled:
        # Reported, not raised: an already-disabled bench is this command's goal state, the same
        # way `fm tools disable` treats it.
        output.print(f"{provider.value} is already disabled on {bench.name}")
        return

    with spinner(output, f"Disabling {provider.value}"):
        apply_newrelic(bench, output, enabled=False)

    output.print(f"Disabled {provider.value} on {bench.name}")
