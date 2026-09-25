"""Basic auth enable command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.auth._helpers import (
    ADDRESS_HELP,
    PANEL_CREDENTIALS,
    PANEL_EXEMPTIONS,
    PANEL_SAFETY,
    PANEL_SURFACES,
    apply_auth,
)
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback, bench_site_callback


@example(
    "Password-protect the whole bench",
    "{benchname}",
    detail="Both surfaces prompt: frappe and socketio, and /adminer/ and /mailpit/. Prints the credentials.",
    benchname="mybench",
)
@example(
    "Protect the site, leaving the admin tools as they are",
    "{benchname} --web",
    detail="Naming a surface acts on that surface only; the other keeps whatever state it had.",
    benchname="mybench",
)
@example(
    "Protect one site of a bench",
    "{benchname}/b.example.com --web",
    detail="That site's hostnames prompt with credentials of its own; the bench's other sites keep serving exactly as before. A site with no auth of its own follows the bench, so 'fm auth enable mybench --web' still covers every site.",
    benchname="mybench",
)
@example(
    "Set your own credentials",
    "{benchname} --user alice --password -",
    detail="Reads the password from stdin, so it never lands in the shell history.",
    benchname="mybench",
)
@example(
    "Let a webhook through",
    "{benchname} --web --allow-path /api/method/payment_webhook",
    detail="Exempt paths replace the stored list; omitting the flag keeps it.",
    benchname="mybench",
)
def enable(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            # NOT the shared `BenchSiteArgument` help. There a bare bench means the bench's primary
            # site; here it means the WHOLE bench, and an operator who read "primary site is used"
            # would think `fm auth enable shop --web` left the other sites open.
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
    user: Annotated[
        str | None,
        typer.Option(
            "--user",
            help="Basic auth username for the scope you named: both surfaces of the bench, or that one site. Defaults to 'admin'.",
            show_default=False,
            rich_help_panel=PANEL_CREDENTIALS,
        ),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="Basic auth password. Pass - to read it from stdin, keeping it out of the shell history. A random one is minted on the first enable.",
            show_default=False,
            rich_help_panel=PANEL_CREDENTIALS,
        ),
    ] = None,
    rotate: Annotated[
        bool,
        typer.Option(
            "--rotate",
            help="Replace the password with a fresh random one, invalidating browser sessions that cached the old one.",
            rich_help_panel=PANEL_CREDENTIALS,
        ),
    ] = False,
    allow_ip: Annotated[
        list[str],
        typer.Option(
            "--allow-ip",
            help="Address or CIDR that skips the prompt (repeatable; replaces the stored list). Behind a CDN this needs real-IP forwarding, see fm services real-ip.",
            show_default=False,
            rich_help_panel=PANEL_EXEMPTIONS,
        ),
    ] = [],
    allow_path: Annotated[
        list[str],
        typer.Option(
            "--allow-path",
            help="Absolute path prefix served without a prompt, e.g. /api/method/payment_webhook (repeatable; replaces the stored list). Web surface only.",
            show_default=False,
            rich_help_panel=PANEL_EXEMPTIONS,
        ),
    ] = [],
    clear_exemptions: Annotated[
        bool,
        typer.Option(
            "--clear-exemptions",
            help="Empty both allow lists. Applied before any --allow-ip/--allow-path in the same call.",
            rich_help_panel=PANEL_EXEMPTIONS,
        ),
    ] = False,
    insecure: Annotated[
        bool,
        typer.Option(
            "--insecure",
            help="Protect the web surface on a bench without TLS anyway, and silence the same warning on the tools surface.",
            rich_help_panel=PANEL_SAFETY,
        ),
    ] = False,
):
    """
    Put an HTTP basic auth prompt in front of a bench: the site, the admin tools, or both.

    --web and --tools select which surfaces this acts on, and naming one says nothing about the other: 'fm auth enable BENCH --web' leaves the admin tools exactly as they were. Naming neither acts on both. Credentials and allow lists are kept when a surface goes off, so re-enabling asks for nothing.

    BENCH/SITE protects the web surface of one site, with credentials of its own, and leaves the bench's other sites serving as before. A site with no auth of its own follows the bench, so 'fm auth enable BENCH' still covers every site. --tools takes no site part: one Adminer and one Mailpit serve the whole bench, on every hostname it has.

    Basic auth sends credentials base64-encoded, not encrypted, so on a bench without TLS they are effectively cleartext: protecting the web surface there needs --insecure. The certificate checked is the one for the hostname you named.
    """

    # Naming neither surface means both, so the bare form reads as "protect this bench".
    both = not web and not tools
    apply_auth(
        ctx,
        address,
        web_target=True if (web or both) else None,
        tools_target=True if (tools or both) else None,
        tools_named=tools,
        user=user,
        password=password,
        rotate=rotate,
        allow_ip=allow_ip,
        allow_path=allow_path,
        clear_exemptions=clear_exemptions,
        insecure=insecure,
    )
