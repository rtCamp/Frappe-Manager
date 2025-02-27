import typer
from typing import Annotated, Optional
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback

def start(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force recreate bench containers")] = False,
    sync_bench_config_changes: Annotated[
        bool, typer.Option("--sync-config", help="Sync bench configuration changes")
    ] = False,
    reconfigure_supervisor: Annotated[
        bool, typer.Option("--reconfigure-supervisor", help="Reconfigure supervisord configuration")
    ] = False,
    reconfigure_common_site_config: Annotated[
        bool, typer.Option("--reconfigure-common-site-config", help="Reconfigure common_site_config.json")
    ] = False,
    reconfigure_workers: Annotated[
        bool, typer.Option("--reconfigure-workers", help="Reconfigure workers configuration")
    ] = False,
    include_default_workers: Annotated[bool, typer.Option(help="Include default worker configuration")] = True,
    include_custom_workers: Annotated[bool, typer.Option(help="Include custom worker configuration")] = True,
    sync_dev_packages: Annotated[bool, typer.Option("--sync-dev-packages", help="Sync dev packages")] = False,
):
    """Start a bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

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
