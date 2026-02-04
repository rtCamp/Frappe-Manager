from typing import Annotated, Optional
import typer
from frappe_manager.logger.context import LoggerContext
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.commands import check_bench_migration_required


def shell(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    command: Annotated[Optional[str], typer.Option("-c", "--command", help="Execute command and exit")] = None,
    user: Annotated[Optional[str], typer.Option(help="User to connect as", show_default=False)] = None,
    service: Annotated[str, typer.Option(help="Service to connect to")] = "frappe",
    shell_path: Annotated[Optional[str], typer.Option(help="Shell path (e.g., /bin/bash, /bin/sh)")] = None,
    run: Annotated[bool, typer.Option(help="Use 'docker compose run --rm'")] = False,
):
    """
    Spawn shell for the bench or execute a command.

    Supports interactive shell mode and command execution mode (use -c or -- syntax).
    Exit code from the executed command is preserved for scripting.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    available_services = bench.get_available_services()
    if service not in available_services:
        output.display_error(f"Service '{service}' not found")
        output.print(f"Available services: {', '.join(sorted(available_services))}")
        raise typer.Exit(1)

    output.stop()

    # Check if we have passthrough arguments (-- syntax)
    passthrough_args = ctx.args if ctx.args else None

    # Determine mode: interactive or command execution
    if command or passthrough_args:
        # Command execution mode
        if passthrough_args:
            # Use passthrough arguments (everything after --)
            exec_command = " ".join(passthrough_args)
        else:
            # Use -c command
            exec_command = command

        exit_code = bench.execute_command(service, exec_command, user, shell_path=shell_path, use_run=run)

        # Exit with the command's exit code
        if exit_code != 0:
            raise typer.Exit(exit_code)
    else:
        # Interactive shell mode (original behavior)
        bench.shell(service, user, shell_path=shell_path, use_run=run)
