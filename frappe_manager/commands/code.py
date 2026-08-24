from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import DEFAULT_EXTENSIONS
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
    ] = "/workspace/frappe-bench",
):
    """
    Open a bench in VSCode, attached to its running frappe container.

    Needs the bench up (--force-start starts it) and the VSCode 'code' CLI on PATH. An image-mode bench has no mounted workspace, so edits made here live only in that container and are lost on the next deploy or switch.
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

    bench.attach_to_bench(user=user, extensions=extensions, workdir=workdir, debugger=debugger)
