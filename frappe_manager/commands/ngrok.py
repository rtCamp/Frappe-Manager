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
    "Tunnel a running bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Supply a token and remember it",
    "{benchname} --auth-token 2abcXYZ --save-token",
    benchname="mybench",
)
def ngrok(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    auth_token: Annotated[
        str | None,
        typer.Option(
            "--auth-token",
            "-t",
            help="ngrok auth token. Falls back to the one saved in fm's config.",
            envvar="NGROK_AUTHTOKEN",
        ),
    ] = None,
    save_token: Annotated[
        bool | None,
        typer.Option(
            "--save-token/--no-save-token",
            help="Save this token to fm's config for later runs, or leave the config alone. fm asks when a new token arrives and neither flag is passed.",
        ),
    ] = None,
):
    """
    Expose a running bench on a public ngrok URL.

    Needs an ngrok auth token: pass --auth-token, set NGROK_AUTHTOKEN, or save one in fm's config.
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
