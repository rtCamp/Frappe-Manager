import typer
from typing import Annotated, Optional
from frappe_manager.site_manager.bench import Bench
from frappe_manager.utils.callbacks import sites_autocompletion_callback, sitename_callback
from frappe_manager.display_manager.DisplayManager import richprint

from frappe_manager.commands import app

@app.command()
def reset(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    site_name: Annotated[
        Optional[str],
        typer.Option("--site", help="Site name to reset", show_default=False),
    ] = None,
    admin_pass: Annotated[
        Optional[str],
        typer.Option(help="Password for the 'Administrator' User."),
    ] = None,
    confirm: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
):
    """Reset bench site and reinstall all installed apps."""

    services_manager = ctx.obj["services"]
    bench = Bench.get_object(benchname, services_manager)
    
    # Handle site selection with safety for multi-site
    if not site_name:
        if len(bench.sites) == 0:
            richprint.exit(f"No sites found in bench {benchname}")
        elif len(bench.sites) == 1:
            site_name = list(bench.sites.keys())[0]
            richprint.print(f"Using site: {site_name}")
        else:
            # Multi-site: require explicit selection
            richprint.print(f"Multiple sites found in bench {benchname}:")
            for site in bench.sites.keys():
                marker = " (default)" if site == bench.get_default_site().name else ""
                richprint.print(f"  - {site}{marker}")
            richprint.exit("Please specify --site <sitename> to reset a specific site")
    
    # Confirm destructive operation
    if not confirm:
        continue_reset = richprint.prompt_ask(
            prompt=f"⚠️  This will completely reset site '{site_name}' and reinstall all apps. Continue?",
            choices=["yes", "no"],
            default="no"
        )
        if continue_reset == "no":
            richprint.print("Reset cancelled")
            return
    
    richprint.print(f"Resetting site: {site_name}")
    bench.reset(site_name, admin_pass)
