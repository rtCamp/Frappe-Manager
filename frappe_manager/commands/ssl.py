import typer
from typing import Annotated, Optional
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.SiteManager import BenchesManager
from frappe_manager.site_manager.bench import Bench
from frappe_manager.site_manager.site_exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.commands import app

ssl_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
app.add_typer(ssl_command, name="ssl", help="Perform operations related to ssl.")


@ssl_command.command()
def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    sitename: Annotated[
        Optional[str],
        typer.Option("--site", help="Site to remove certificate from")
    ] = None,
):  
    """Delete site SSL certificate."""

    services_manager = ctx.obj["services"]
    bench = Bench.get_object(benchname, services_manager)
    site = bench.get_site(sitename) if sitename else bench.get_default_site()
    
    if not site:
        richprint.exit(f"Site {sitename or 'default'} not found in bench {benchname}")
        
    richprint.change_head(f"Removing SSL certificate for site {site.name}")

    if not site.has_certificate():
        richprint.exit(f"Site {site.name} doesn't have SSL certificate issued.")
    site.remove_certificate()
    richprint.print(f"Removed SSL certificate for site {site.name}.")


@ssl_command.command()
def renew(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback),
    ] = None,
    all: Annotated[bool, typer.Option(help="Renew ssl cert for all benches.")] = False,
):
    """Renew bench ssl certficate."""

    services_manager = ctx.obj["services"]

    benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager)

    if all:
        sites_list = benches.get_all_bench().keys()
    else:
        sites_list = [benchname]

    for benchname in sites_list:
        bench = Bench.get_object(benchname, services_manager)
        richprint.change_head("Renew certificate")
        try:
            bench.renew_certificate()
        except (BenchSSLCertificateNotIssued, SSLCertificateNotDueForRenewalError) as e:
            richprint.warning(e.message)

        except Exception as e:
            richprint.warning(str(e))
