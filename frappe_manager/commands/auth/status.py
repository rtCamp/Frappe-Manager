"""Basic auth status command."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands.auth._helpers import ADDRESS_HELP, print_state, resolve_scope
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.callbacks import bench_site_autocompletion_callback, bench_site_callback


@example(
    "Show which surfaces of a bench ask for a password",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Show one site's own auth",
    "{benchname}/b.example.com",
    detail="A site with no auth of its own reports the bench's, and says so.",
    benchname="mybench",
)
def status(
    ctx: typer.Context,
    address: Annotated[
        str | None,
        typer.Argument(
            metavar="BENCH(/SITE)",
            # NOT the shared `BenchSiteArgument` help. There a bare bench means the bench's primary
            # site; here it means the WHOLE bench, and an operator who read "primary site is used"
            # would think the answer covered only one site.
            help=ADDRESS_HELP,
            autocompletion=bench_site_autocompletion_callback,
            callback=bench_site_callback,
        ),
    ] = None,
):
    """
    Report which surfaces are protected, with the credentials and allow lists while a surface is protected.

    Writes nothing. A site with no auth of its own follows the bench, and is reported as inherited.
    """

    output = get_global_output_handler()
    bench, site, entry = resolve_scope(ctx, address, output)

    scope = f"{bench.name}/{site}" if site else bench.name
    stored = entry.auth if entry is not None else bench.bench_config.auth

    if stored is None:
        if site:
            # Not "unconfigured": the site IS protected or not, by the bench's setting. Report
            # what it actually serves, and say where the answer came from.
            output.print(f"Basic auth for {site}: inherited from bench '{bench.name}'")
            print_state(output, bench.bench_config.auth_for(site), hint_when_off=True)
            output.print(f"  give this site its own with 'fm auth enable {scope} --web'")
            return
        output.print("Basic auth: not configured; bench defaults apply (tools protected, web open)")
        output.print(f"Protect a surface with 'fm auth enable {bench.name} --web' to mint credentials")
        return

    if site:
        output.print(f"Basic auth for {site}: its own, overriding bench '{bench.name}'")
    print_state(output, stored, hint_when_off=True)
