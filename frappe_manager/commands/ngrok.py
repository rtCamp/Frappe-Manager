from typing import Annotated, Optional

import typer

from frappe_manager.logger.context import LoggerContext
from frappe_manager.site_manager.site import Bench
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager.context_managers import spinner, temporary_stop
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
    save_token: Annotated[
        Optional[bool],
        typer.Option(
            "--save-token/--no-save-token", help="Save or don't save the ngrok auth token to config for future use"
        ),
    ] = None,
):
    """Create ngrok tunnel for bench"""
    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]

    with spinner(output, "Setting up ngrok tunnel"):
        if not auth_token and fm_config_manager.ngrok_auth_token:
            auth_token = fm_config_manager.ngrok_auth_token
            output.print("Using ngrok auth token from config file", emoji_code=":key:")
        elif not auth_token:
            output.display_error(
                "Ngrok auth token is required. Please provide it with --auth-token or set NGROK_AUTHTOKEN environment variable."
            )
            raise typer.Exit(1)

        if auth_token and not fm_config_manager.ngrok_auth_token:
            output.print("New auth token provided", emoji_code=":new:")

            if save_token is None:
                with temporary_stop(output):
                    should_save = output.prompt_ask(
                        prompt="Do you want to save the ngrok auth token in config for future use?",
                        choices=['yes', 'no'],
                        required_flag="--save-token or --no-save-token",
                    )
            else:
                should_save = 'yes' if save_token else 'no'

            if should_save == 'yes':
                output.print("Saving auth token to config...", emoji_code=":floppy_disk:")
                fm_config_manager.ngrok_auth_token = auth_token
                fm_config_manager.export_to_toml()
                output.print("Saved ngrok auth token to config", emoji_code=":white_check_mark:")

        output.print(f"Creating ngrok tunnel for {bench.name}", emoji_code=":link:")

        try:
            create_tunnel(bench.name, auth_token)
        except Exception as e:
            output.display_error(f"Failed to create tunnel: {str(e)}")
            raise
