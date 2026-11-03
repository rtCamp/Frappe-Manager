import json as json_module
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import (
    CLI_BENCHES_DIRECTORY,
)
from frappe_manager.commands.arguments import JsonFieldsOption, JsonResultOption, _apply_fields
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
    detail="fm list --json | jq -r '.result[] | select(.status == \"active\") | .name'",
)
def list(
    ctx: typer.Context,
    json_output: JsonResultOption = False,
    fields: JsonFieldsOption = None,
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

    if paths and (json_output or (ctx.obj.get("json") if ctx.obj else False)):
        # The json branch below returns without ever reading --paths; refusing beats
        # silently preferring one form (the JSON rows already carry name and path).
        output.error(
            "--paths cannot be combined with --json (the JSON rows already carry name and path)",
            exception=typer.Exit(code=2),
        )

    if json_output or ctx.obj.get("json"):
        get_global_output_handler().set_json_results()
        _apply_fields(fields)
        # Under global --json the JSONL stream owns stdout and the inventory rides it as an event;
        # with the --json flag alone on a human terminal it's a pretty dump. Both go through the
        # data channel, which decides the rendering per handler.
        data = bench_service.list_benches_data()
        # One channel for both modes: the handler decides the rendering, which is what puts a
        # result in the same envelope its failure uses.
        output.print_data(data)
        return

    if paths:
        # Copy targets get PLAIN lines, not table cells: rich cells truncate or
        # fold (both corrupt a copied path); plain lines soft-wrap and pipe.
        data = bench_service.list_benches_data()
        width = max((len(b["name"]) for b in data), default=0)
        for b in data:
            output.data_raw(f"{b['name']:<{width}}  {b['path']}")
        return

    view = bench_service.list_benches_view()
    if view is not None:
        output.print_data(view)
