import typer
from typing import Annotated, Optional
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.SiteManager import BenchesManager
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.display_manager.DisplayManager import richprint

ssl_root_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

@ssl_root_command.command()
def delete(
        ctx: typer.Context,
        benchname: Annotated[Optional[str], typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback)] = None,
    ):
    services_manager = ctx.obj["services"]

    bench = Bench.get_object(benchname,services_manager)

    richprint.change_head("Removing SSL certificate")

    if not bench.has_certificate():
        richprint.exit(f"{benchname} doesn't have SSL certificate.")

    bench.remove_certificate(bench.get_certificate_manager())
    richprint.print("Removing SSL certificate: Done")

@ssl_root_command.command()
def renew(
        ctx: typer.Context,
        benchname: Annotated[Optional[str], typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback)] = None,
        all: Annotated[bool, typer.Option(help="Renew ssl cert for all benches.")] = False,
    ):
        services_manager = ctx.obj["services"]

        benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager)

        if all:
            sites_list = benches.get_all_bench().keys()
        else:
            sites_list = [benchname]

        for benchname in sites_list:
            bench = Bench.get_object(benchname,services_manager)

            if not bench.compose_project.is_service_running('nginx'):
                richprint.error(f"[blue]{bench.name}[/blue] is not running. Skipping...")
                break

            if not bench.has_certificate():
                richprint.error(f"[blue]{bench.name}[/blue] doesn't have certificate issued. Skipping...")
                break

            richprint.change_head("Renew certificate")
            bench.renew_certificate(bench.get_certificate_manager())
