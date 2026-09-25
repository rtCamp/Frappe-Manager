"""Basic auth disable command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.auth._helpers import ADDRESS_HELP, PANEL_SURFACES, apply_auth
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback, bench_site_callback


@example(
    "Stop asking for a password anywhere on the bench",
    "{benchname}",
    detail="Both surfaces stop prompting. The credentials stay stored and apply again on the next enable.",
    benchname="mybench",
)
@example(
    "Open the site, keeping the admin tools protected",
    "{benchname} --web",
    detail="Naming a surface acts on that surface only; the other keeps whatever state it had.",
    benchname="mybench",
)
@example(
    "Stop one site prompting",
    "{benchname}/b.example.com",
    benchname="mybench",
)
def disable(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            help=ADDRESS_HELP,
            autocompletion=bench_site_autocompletion_callback,
            callback=bench_site_callback,
        ),
    ] = None,
    web: Annotated[
        bool,
        typer.Option(
            "--web",
            help="Act on the web surface: frappe and socketio.",
            rich_help_panel=PANEL_SURFACES,
        ),
    ] = False,
    tools: Annotated[
        bool,
        typer.Option(
            "--tools",
            help="Act on the admin tools surface: /adminer/ and /mailpit/. Bench-wide, so it takes no site part.",
            rich_help_panel=PANEL_SURFACES,
        ),
    ] = False,
):
    """
    Stop a surface asking for a password, keeping the credentials for later.

    --web and --tools select which surfaces this acts on, and naming one says nothing about the other: 'fm auth disable BENCH --web' leaves the admin tools prompting. Naming neither acts on both.

    Credentials and allow lists are kept, so 'fm auth enable' afterwards asks for nothing.
    """

    both = not web and not tools
    apply_auth(
        ctx,
        address,
        web_target=False if (web or both) else None,
        tools_target=False if (tools or both) else None,
        tools_named=tools,
    )
