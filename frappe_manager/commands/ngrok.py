from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchServedDomainArgument
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
    address: BenchServedDomainArgument = None,
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

    The tunnel rewrites the Host header on every request, so it reaches exactly one of the bench's hostnames: fm ngrok BENCH/DOMAIN reaches that one, and a bare fm ngrok BENCH reaches the bench's primary site. A bench serving several sites needs the domain named, because one tunnel cannot answer for all of them.

    Needs an ngrok auth token: pass --auth-token, set NGROK_AUTHTOKEN, or save one in fm's config.
    """
    check_bench_migration_required(address)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(address, services_manager, output_handler=output)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    # The one hostname this tunnel answers for. Checked here rather than in the callback because the
    # refusal can name what the bench actually serves only with the config loaded.
    requested = ctx.obj.get("domain") if ctx.obj else None
    served = list(bench.bench_config.domains)
    if requested and requested not in served:
        output.display_error(
            f"bench '{bench.name}' does not serve '{requested}'. It serves {', '.join(repr(d) for d in served)}."
        )
        raise typer.Exit(1)
    tunnel_host = requested or bench.primary_domain

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

        output.print(f"Creating ngrok tunnel for {tunnel_host}", emoji_code=":link:")

        try:
            create_tunnel(tunnel_host, auth_token)
        except Exception as e:
            output.display_error(f"Failed to create tunnel: {e!s}")
            raise
