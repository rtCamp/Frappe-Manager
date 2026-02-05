from typing import Annotated

import typer

from frappe_manager import DEFAULT_EXTENSIONS
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    code_command_extensions_callback,
    sitename_callback,
    sites_autocompletion_callback,
)


def code(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback,
        ),
    ] = None,
    user: Annotated[str, typer.Option(help="User to connect as")] = "frappe",
    extensions: Annotated[
        list[str],
        typer.Option(
            "--extension",
            "-e",
            help="VSCode extensions to install (e.g., ms-python.python)",
            callback=code_command_extensions_callback,
            show_default=False,
        ),
    ] = DEFAULT_EXTENSIONS,
    force_start: Annotated[bool, typer.Option("--force-start", "-f", help="Start bench before opening VSCode")] = False,
    debugger: Annotated[bool, typer.Option("--debugger", "-d", help="Setup debugger config")] = False,
    workdir: Annotated[
        str, typer.Option("--work-dir", "-w", help="Working directory in VSCode"),
    ] = "/workspace/frappe-bench",
):
    """Open bench in vscode."""

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    if force_start:
        bench.start()

    bench.attach_to_bench(user=user, extensions=extensions, workdir=workdir, debugger=debugger)
