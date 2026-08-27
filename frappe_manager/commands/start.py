from typing import Annotated

import typer
from click.core import ParameterSource
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
        bool, typer.Option(help="Include the default workers when regenerating. Needs --reconfigure-workers.")
    ] = True,
    include_custom_workers: Annotated[
        bool, typer.Option(help="Include custom workers when regenerating. Needs --reconfigure-workers.")
    ] = True,
    sync_dev_packages: Annotated[
        bool,
        typer.Option("--sync-dev-packages", help="Install dev packages on a dev bench, remove them on a prod one."),
    ] = False,
):
    """
    Start a bench's containers, admin tools and workers.
    """

    output = get_global_output_handler()

    # Pure flag guards, before any bench lookup. Both worker-scope flags are read only while the
    # workers compose file is being regenerated, so a request fm cannot act on is refused instead
    # of accepted and dropped.
    if not include_default_workers and not include_custom_workers:
        output.error(
            "--no-include-default-workers with --no-include-custom-workers regenerates an EMPTY worker set, "
            "which deletes docker-compose.workers.yml and leaves the bench with no workers at all: keep one "
            "of the two.",
            exception=typer.Exit(code=1),
        )

    worker_scope_flags = [
        f"--{name.replace('_', '-')}"
        for name in ("include_default_workers", "include_custom_workers")
        if ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE
    ]
    if worker_scope_flags and not reconfigure_workers:
        output.error(
            f"{' and '.join(worker_scope_flags)} only apply with --reconfigure-workers, which is what "
            "regenerates docker-compose.workers.yml; on its own fm start leaves the worker set exactly "
            "as it is already configured.",
            exception=typer.Exit(code=1),
        )

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]

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
