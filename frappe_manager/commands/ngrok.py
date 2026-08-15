from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.ngrok import create_tunnel
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.output_manager.context_managers import spinner, temporary_stop
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.site import Bench


@example(
    "Create ngrok tunnel for bench",
    "{benchname} --auth-token YOUR_TOKEN",
    detail="Creates a public ngrok tunnel for the bench using a specified auth token.",
    benchname="mybench",
)
@example(
    "Use saved auth token from config",
    "{benchname}",
    detail="Uses an auth token stored in FM configuration to create the tunnel without passing it on the command line.",
    benchname="mybench",
)
def ngrok(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    auth_token: Annotated[
        str | None,
        typer.Option("--auth-token", "-t", help="Ngrok authentication token", envvar="NGROK_AUTHTOKEN"),
    ] = None,
    save_token: Annotated[
        bool | None,
        typer.Option(
            "--save-token/--no-save-token",
            help="Save or don't save the ngrok auth token to config for future use",
        ),
    ] = None,
):
    """
    Create ngrok tunnel for bench.

    Provisions a public URL for local benches using ngrok; requires an auth token either via flag or config.
    """
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

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
                "Ngrok auth token is required. Please provide it with --auth-token or set NGROK_AUTHTOKEN environment variable.",
            )
            raise typer.Exit(1)

        # Guarded on "different from what is stored", not "nothing is stored": an explicit
        # --save-token with a replacement token used to be discarded in silence.
        if auth_token != fm_config_manager.ngrok_auth_token:
            if fm_config_manager.ngrok_auth_token:
                output.print("Replacing saved auth token", emoji_code=":new:")
            else:
                output.print("New auth token provided", emoji_code=":new:")

            if save_token is None:
                with temporary_stop(output):
                    should_save = output.prompt_ask(
                        prompt="Do you want to save the ngrok auth token in config for future use?",
                        choices=["yes", "no"],
                        required_flag="--save-token or --no-save-token",
                    )
            else:
                should_save = "yes" if save_token else "no"

            if should_save == "yes":
                output.print("Saving auth token to config...", emoji_code=":floppy_disk:")
                fm_config_manager.ngrok_auth_token = auth_token
                fm_config_manager.export_to_toml()
                output.print("Saved ngrok auth token to config", emoji_code=":white_check_mark:")

        output.print(f"Creating ngrok tunnel for {bench.name}", emoji_code=":link:")

        try:
            create_tunnel(bench.name, auth_token)
        except Exception as e:
            output.display_error(f"Failed to create tunnel: {e!s}")
            raise
