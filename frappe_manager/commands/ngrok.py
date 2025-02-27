import typer
from typing import Annotated, Optional
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.site_exceptions import BenchNotRunning
from frappe_manager.utils.callbacks import sites_autocompletion_callback
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.ngrok import create_tunnel

def ngrok(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        typer.Option("--auth-token", "-t", help="Ngrok authentication token", envvar="NGROK_AUTHTOKEN"),
    ] = None,
):
    """Create ngrok tunnel for the bench."""
    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

    if not bench.compose_project.running:
        raise BenchNotRunning(bench_name=bench.name)

    fm_config_manager = ctx.obj["fm_config_manager"]

    richprint.start("Setting up ngrok tunnel")

    # Use token from config if available and no token provided
    if not auth_token and fm_config_manager.ngrok_auth_token:
        auth_token = fm_config_manager.ngrok_auth_token
        richprint.print("Using ngrok auth token from config file", emoji_code=":key:")
    elif not auth_token:
        richprint.exit(
            "Ngrok auth token is required. Please provide it with --auth-token or set NGROK_AUTHTOKEN environment variable."
        )

    # If token provided and not in config, ask to save
    if auth_token and not fm_config_manager.ngrok_auth_token:
        richprint.print("New auth token provided", emoji_code=":new:")
        should_save = richprint.prompt_ask(
            prompt="Do you want to save the ngrok auth token in config for future use?",
            choices=['yes', 'no'],
        )
        if should_save == 'yes':
            richprint.print("Saving auth token to config...", emoji_code=":floppy_disk:")
            fm_config_manager.ngrok_auth_token = auth_token
            fm_config_manager.export_to_toml()
            richprint.print("Saved ngrok auth token to config", emoji_code=":white_check_mark:")

    richprint.print(f"Creating ngrok tunnel for {bench.name}", emoji_code=":link:")

    try:
        create_tunnel(bench.name, auth_token)
    except Exception as e:
        richprint.error(f"Failed to create tunnel: {str(e)}")
        raise
