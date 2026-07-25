from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    sitename_callback,
    sites_autocompletion_callback,
)


@example(
    "Start bench containers",
    "{benchname}",
    detail="Starts all containers for the specified bench. Useful to bring a bench up after stopping or system reboot.",
    benchname="mybench",
)
@example(
    "Force recreate containers",
    "{benchname} --force",
    detail="Recreates containers even if they already exist; use when container images or configuration changed.",
    benchname="mybench",
)
@example(
    "Start and reconfigure workers",
    "{benchname} --reconfigure-workers",
    detail="Starts the bench and reconfigures worker processes to pick up configuration changes.",
    benchname="mybench",
)
@example(
    "Start with supervisor reconfiguration",
    "{benchname} --reconfigure-supervisor",
    detail="Reconfigures the supervisor process manager during start to update process definitions.",
    benchname="mybench",
)
@example(
    "Start and sync dev packages",
    "{benchname} --sync-dev-packages",
    detail="Synchronizes development packages after starting; useful in development workflows.",
    benchname="mybench",
)
def start(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Recreate containers")] = False,
    reconfigure_supervisor: Annotated[
        bool,
        typer.Option("--reconfigure-supervisor", help="Reconfigure supervisor"),
    ] = False,
    reconfigure_common_site_config: Annotated[
        bool,
        typer.Option("--reconfigure-common-site-config", help="Reconfigure site config"),
    ] = False,
    reconfigure_workers: Annotated[bool, typer.Option("--reconfigure-workers", help="Reconfigure workers")] = False,
    include_default_workers: Annotated[bool, typer.Option(help="Include default workers")] = True,
    include_custom_workers: Annotated[bool, typer.Option(help="Include custom workers")] = True,
    sync_dev_packages: Annotated[bool, typer.Option("--sync-dev-packages", help="Sync dev packages")] = False,
):
    """
    Start a bench.

    Starts all containers for the specified bench. Reconfigure flags allow updating process and worker settings.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    with spinner(output, f"Starting {benchname}"):
        bench.start(
            force=force,
            reconfigure_workers=reconfigure_workers,
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers,
            reconfigure_common_site_config=reconfigure_common_site_config,
            reconfigure_supervisor=reconfigure_supervisor,
            sync_dev_packages=sync_dev_packages,
        )
