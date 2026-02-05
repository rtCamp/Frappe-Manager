from typing import Annotated

import typer

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback


def shell(
    ctx: typer.Context,
    benchname: Annotated[
        str | None,
        typer.Argument(
            help="Name of the bench.",
            autocompletion=sites_autocompletion_callback,
            callback=sitename_callback,
        ),
    ] = None,
    command: Annotated[str | None, typer.Option("-c", "--command", help="Execute command and exit")] = None,
    user: Annotated[str | None, typer.Option(help="User to connect as", show_default=False)] = None,
    service: Annotated[str, typer.Option(help="Service to connect to")] = "frappe",
    shell_path: Annotated[str | None, typer.Option(help="Shell path (e.g., /bin/bash, /bin/sh)")] = None,
    run: Annotated[bool, typer.Option(help="Use 'docker compose run --rm'")] = False,
):
    """
    Spawn shell for the bench or execute a command.

    In interactive mode, provides full terminal support for commands like 'bench console'.
    In non-interactive mode (-n flag or piped input), captures output for scripting.
    Exit code from the executed command is preserved.
    """

    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]

    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    available_services = bench.get_available_services()
    if service not in available_services:
        output.display_error(f"Service '{service}' not found")
        output.print(f"Available services: {', '.join(sorted(available_services))}")
        raise typer.Exit(1)

    output.stop()

    passthrough_args = ctx.args if ctx.args else None
    is_interactive = output.is_interactive()

    if command or passthrough_args:
        if passthrough_args:
            if service == "frappe" and not user:
                user = "frappe"

            if not shell_path:
                non_bash_supported = ["redis-cache", "redis-queue", "adminer", "mailpit"]
                shell_path = "/bin/bash" if service not in non_bash_supported else "sh"

            if run:
                exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["run", "--rm"]
                if user:
                    exec_cmd += ["--user", user]
                exec_cmd += ["--entrypoint", shell_path]
                exec_cmd += [service, "-c", " ".join(passthrough_args)]
            else:
                exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["exec"]
                if user:
                    exec_cmd += ["--user", user]
                if service == "frappe":
                    exec_cmd += ["--workdir", "/workspace/frappe-bench"]
                exec_cmd += [service, shell_path, "-c", " ".join(passthrough_args)]

            if is_interactive:
                import os

                os.execvp(exec_cmd[0], exec_cmd)
            else:
                command_str = " ".join(passthrough_args)
                exit_code = bench.execute_command(service, command_str, user, shell_path=shell_path, use_run=run)

                if exit_code != 0:
                    raise typer.Exit(exit_code)
        else:
            exit_code = bench.execute_command(service, command, user, shell_path=shell_path, use_run=run)

            if exit_code != 0:
                raise typer.Exit(exit_code)
    else:
        bench.shell(service, user, shell_path=shell_path, use_run=run)
