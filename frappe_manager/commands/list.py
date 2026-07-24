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
    "List all available benches",
    "",
    detail="Shows a table of all benches managed by FM with status, runtime, apps and deploy info.",
)
@example(
    "Machine-readable output",
    "--json",
    detail="Emits the full bench inventory as JSON (status, runtime, environment, apps, tags, domains, policies) for scripting: fm list --json | jq '.[].name'.",
)
def list(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output the bench inventory as JSON (clean stdout, pipe-friendly)."),
    ] = False,
    paths: Annotated[
        bool,
        typer.Option(
            "--paths",
            "-p",
            help="Plain 'bench  path' lines (no table): copy- and pipe-friendly, never truncated.",
        ),
    ] = False,
):
    """
    List all benches.

    Shows a table with status, runtime (mount/image), environment, installed apps,
    the deployed tag / base image, and path. --json emits the full inventory
    (including alias domains, seed provenance, restart policy) for scripting.
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
