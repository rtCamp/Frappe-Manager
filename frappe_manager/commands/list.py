import json as json_module
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import (
    CLI_BENCHES_DIRECTORY,
)
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_service import BenchService


@example(
    "List every bench",
    "",
)
@example(
    "Copy or pipe bench paths",
    "--paths",
)
@example(
    "Script over the inventory",
    "--json",
    detail="fm list --json | jq -r '.[] | select(.status == \"active\") | .name'",
)
def list(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the full inventory as JSON on clean stdout."),
    ] = False,
    paths: Annotated[
        bool,
        typer.Option(
            "--paths",
            "-p",
            help="Print plain 'name  path' lines instead of cards, so paths survive copying and piping.",
        ),
    ] = False,
):
    """
    List all benches with status, runtime, installed apps and deploy state.

    A bench whose config will not load is reported as a warning and left out of the listing; every other bench still lists. --json includes it instead, as a row carrying the error.
    """

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)

    if json_output:
        output.stop()  # keep stdout clean for piping
        data = bench_service.list_benches_data()
        typer.echo(json_module.dumps(data, indent=2))
        return

    if paths:
        # Copy targets get PLAIN lines, not table cells: rich cells truncate or
        # fold (both corrupt a copied path); plain lines soft-wrap and pipe.
        output.stop()
        data = bench_service.list_benches_data()
        width = max((len(b["name"]) for b in data), default=0)
        for b in data:
            typer.echo(f"{b['name']:<{width}}  {b['path']}")
        return

    view = bench_service.list_benches_view()
    if view is not None:
        output.print_data(view)
