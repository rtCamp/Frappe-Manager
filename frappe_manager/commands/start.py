from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.site_manager.site import Bench


@example(
    "Start a bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Recreate the containers",
    "{benchname} --force",
    detail="Use after an image or compose change.",
    benchname="mybench",
)
@example(
    "Pick up worker config changes",
    "{benchname} --reconfigure-workers",
    benchname="mybench",
)
def start(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Recreate the containers instead of reusing the existing ones.")
    ] = False,
    reconfigure_supervisor: Annotated[
        bool,
        typer.Option("--reconfigure-supervisor", help="Regenerate the supervisord config before starting processes."),
    ] = False,
    reconfigure_common_site_config: Annotated[
        bool,
        typer.Option("--reconfigure-common-site-config", help="Rewrite common_site_config.json with fm's defaults."),
    ] = False,
    reconfigure_workers: Annotated[
        bool,
        typer.Option("--reconfigure-workers", help="Regenerate the workers compose file from the bench config."),
    ] = False,
    include_default_workers: Annotated[
        bool, typer.Option(help="Include the default workers when regenerating.")
    ] = True,
    include_custom_workers: Annotated[bool, typer.Option(help="Include custom workers when regenerating.")] = True,
    sync_dev_packages: Annotated[
        bool,
        typer.Option("--sync-dev-packages", help="Install dev packages on a dev bench, remove them on a prod one."),
    ] = False,
):
    """
    Start a bench's containers, admin tools and workers.
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
