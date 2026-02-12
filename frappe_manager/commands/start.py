from typing import Annotated

import typer

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    sitename_callback,
    sites_autocompletion_callback,
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
    sync_bench_config_changes: Annotated[bool, typer.Option("--sync-config", help="Sync config changes")] = False,
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

    Starts all containers for the specified bench. Use --force to recreate containers.
    Various --reconfigure options allow syncing configuration changes without full restart.
    Use --sync-config to apply bench config changes, --reconfigure-supervisor for process management,
    and --reconfigure-workers to update worker configurations.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]
    logger = ctx.obj.get("logger")

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    with spinner(output, f"Starting {benchname}"):
        bench.start(
            force=force,
            sync_bench_config_changes=sync_bench_config_changes,
            reconfigure_workers=reconfigure_workers,
            include_default_workers=include_default_workers,
            include_custom_workers=include_custom_workers,
            reconfigure_common_site_config=reconfigure_common_site_config,
            reconfigure_supervisor=reconfigure_supervisor,
            sync_dev_packages=sync_dev_packages,
        )
