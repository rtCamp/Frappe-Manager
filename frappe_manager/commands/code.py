from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import CONTAINER_BENCH_DIR, DEFAULT_EXTENSIONS
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import code_command_extensions_callback


@example(
    "Open the bench in VSCode",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Open it with the Frappe debug config",
    "{benchname} --debugger",
    benchname="mybench",
)
@example(
    "Add your own extension",
    "{benchname} -e vscodevim.vim",
    benchname="mybench",
)
def code(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    user: Annotated[str, typer.Option(help="User VSCode connects as inside the container.")] = "frappe",
    extensions: Annotated[
        list[str],
        typer.Option(
            "--extension",
            "-e",
            help="Extra VSCode extension to install alongside fm's defaults, e.g. ms-python.python (repeatable).",
            callback=code_command_extensions_callback,
            show_default=False,
        ),
    ] = DEFAULT_EXTENSIONS,
    force_start: Annotated[
        bool,
        typer.Option("--force-start", "-f", help="Start the bench first if it is not running."),
    ] = False,
    debugger: Annotated[
        bool,
        typer.Option(
            "--debugger",
            "-d",
            help="Write the Frappe debug launch config and install ruff in the container. Workspace directories only.",
        ),
    ] = False,
    workdir: Annotated[
        str,
        typer.Option("--work-dir", "-w", help="Directory VSCode opens inside the container."),
    ] = CONTAINER_BENCH_DIR,
    attach: Annotated[
        bool,
        typer.Option(
            "--attach/--no-attach",
            help="Launch VSCode on THIS machine and attach it to the bench's container. --no-attach prepares the bench and stops, which is what a server wants: connect later with Remote-SSH plus 'Dev Containers: Attach to Running Container'. Without the flag fm attaches when the 'code' CLI is available and prepares-and-explains when it is not.",
        ),
    ] = True,
):
    """
    Prepare a bench for VSCode and attach to its running frappe container.

    Preparing is the durable half and always happens: the container carries fm's devcontainer metadata (extensions, the user VSCode runs as, editor settings), and VSCode applies it however you connect. Needs the bench up; --force-start starts it.

    Attaching launches VSCode on THIS machine, so it needs the 'code' CLI on PATH. Without it fm prepares the bench and tells you how to connect from another one -- Remote-SSH to this host, then 'Dev Containers: Attach to Running Container' -- which is the normal way to use a bench on a server. --no-attach asks for that explicitly.

    --debugger writes the debug configuration and installs ruff. An image-mode bench has no mounted workspace, so both live inside that container only and are lost on the next deploy or switch.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if bench.bench_config.runtime == BenchRuntime.image:
        output.warning(
            "Image-mode bench has no live-mounted workspace: VSCode edits target the "
            "immutable app image and do not persist. Use this to reproduce+observe only; "
            "ship real code changes with 'fm bake' then 'fm switch'.",
        )

    if force_start:
        bench.start()

    bench.attach_to_bench(user=user, extensions=extensions, workdir=workdir, debugger=debugger, attach=attach)
