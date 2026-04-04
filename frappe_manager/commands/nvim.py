from typing import Annotated

import typer

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    sitename_callback,
    sites_autocompletion_callback,
)


def nvim(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ] = None,
    debugger: Annotated[
        bool,
        typer.Option(
            "--debugger",
            "-d",
            help="Write nvim-dap Lua configuration (.nvim.lua) for Frappe debugging",
        ),
    ] = False,
    workdir: Annotated[
        str,
        typer.Option(
            "--work-dir",
            "-w",
            help="Bench working directory inside the container",
        ),
    ] = "/workspace/frappe-bench",
):
    """Configure Neovim for bench development.

    When [bold cyan]--debugger[/bold cyan] is supplied, writes a [bold].nvim.lua[/bold] project-local
    file at the root of the bench workspace. Neovim loads this file
    automatically (requires [cyan]vim.opt.exrc = true[/cyan] in your init) and
    registers three [bold]nvim-dap[/bold] debug configurations:

    \b
    - [green]fm-frappe-debug[/green]        – Frappe dev-server with debugpy attached
    - [green]Debug Specific Queue[/green]   – attach debugpy to a background worker
    - [green]Debug Specific Function[/green]– run a single frappe.execute() call

    Prerequisites (add to your Neovim config):
    [dim]  mfussenegger/nvim-dap[/dim]
    [dim]  mfussenegger/nvim-dap-python  (optional)[/dim]
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    logger = ctx.obj.get("logger")

    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    if debugger:
        bench.setup_neovim_debugger(workdir=workdir)
    else:
        output.print(
            "No action specified. Use [bold cyan]--debugger[/bold cyan] to write the nvim-dap configuration.",
        )
