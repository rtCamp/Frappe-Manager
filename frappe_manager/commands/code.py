import typer
from typing import Annotated, List, Optional
from frappe_manager import DEFAULT_EXTENSIONS
from frappe_manager.site_manager.bench import Bench
from frappe_manager.utils.callbacks import (
    sites_autocompletion_callback,
    sitename_callback,
    code_command_extensions_callback
)

from frappe_manager.commands import app

@app.command()
def code(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    user: Annotated[str, typer.Option(help="Connect as this user.")] = "frappe",
    extensions: Annotated[
        List[str],
        typer.Option(
            "--extension",
            "-e",
            help="List of extensions to install in vscode at startup.Provide extension id eg: ms-python.python",
            callback=code_command_extensions_callback,
            show_default=False,
        ),
    ] = DEFAULT_EXTENSIONS,
    force_start: Annotated[
        bool, typer.Option("--force-start", "-f", help="Force start the site before attaching to container.")
    ] = False,
    debugger: Annotated[bool, typer.Option("--debugger", "-d", help="Sync vscode debugger configuration.")] = False,
    workdir: Annotated[
        str, typer.Option("--work-dir", "-w", help="Set working directory in vscode.")
    ] = '/workspace/frappe-bench',
):
    """Open bench in vscode."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    bench = Bench.get_object(benchname, services_manager)

    if force_start:
        bench.start()

    bench.attach_to_bench(user=user, extensions=extensions, workdir=workdir, debugger=debugger)
