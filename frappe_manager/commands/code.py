from typing import Annotated

import typer
from typer_examples import example

from frappe_manager import DEFAULT_EXTENSIONS
from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    code_command_extensions_callback,
    sitename_callback,
    sites_autocompletion_callback,
)


@example(
    "Open bench in VSCode",
    "{benchname}",
    detail="Opens the bench workspace in VSCode and attaches the recommended extensions and settings.",
    benchname="mybench",
)
@example(
    "Open bench with debugger config",
    "{benchname} --debugger",
    detail="Launches VSCode with debugger configuration prepared for the Frappe app.",
    benchname="mybench",
)
@example(
    "Force start bench before opening",
    "{benchname} --force-start",
    detail="Starts the bench containers before opening VSCode if they are not running.",
    benchname="mybench",
)
@example(
    "Add custom VSCode extension",
    "{benchname} --extension vscodevim.vim",
    detail="Installs or enables additional VSCode extensions inside the development container.",
    benchname="mybench",
)
@example(
    "Open with custom working directory",
    "{benchname} --work-dir /workspace",
    detail="Overrides the default working directory used within the VSCode container.",
    benchname="mybench",
)
def code(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
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
        str,
        typer.Option("--work-dir", "-w", help="Working directory in VSCode"),
    ] = "/workspace/frappe-bench",
):
    """
    Open bench in VSCode.

    Attaches VSCode to the bench container with recommended extensions and optional debugger support.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    if bench.bench_config.runtime == BenchRuntime.image:
        output.warning(
            "Image-mode bench has no live-mounted workspace: VSCode edits target the "
            "immutable app image and do not persist. Use this to reproduce+observe only; "
            "ship real code changes with 'fm deploy'.",
        )

    if force_start:
        bench.start()

    bench.attach_to_bench(user=user, extensions=extensions, workdir=workdir, debugger=debugger)
