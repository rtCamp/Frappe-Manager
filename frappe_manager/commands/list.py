import typer
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.SiteManager import BenchesManager

def list_benches(ctx: typer.Context):
    """Lists all of the available benches."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager, verbose=verbose)
    benches.set_typer_context(ctx)
    benches.list_benches()
