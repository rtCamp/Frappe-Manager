import base64
import os
import sys
from typing import Annotated

import typer

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager import NON_BASH_SUPPORTED_SERVICES
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback


def _get_default_user(service: str, user: str | None) -> str | None:
    if service == "frappe" and not user:
        return "frappe"
    return user


def _get_default_shell_path(service: str, shell_path: str | None) -> str:
    if shell_path:
        return shell_path
    return "/bin/bash" if service not in NON_BASH_SUPPORTED_SERVICES else "sh"


def _handle_bench_console(
    bench: Bench,
    benchname: str,
    command: str | None,
    site: str | None,
    user: str | None,
    run: bool,
    output,
) -> None:
    if not site:
        site = benchname

    python_code = None

    if command:
        python_code = command
    elif not sys.stdin.isatty():
        python_code = sys.stdin.read()
    else:
        if run:
            exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["run", "--rm"]
            # Let entrypoint handle UID remapping via exec-command.sh wrapper
            exec_cmd += ["frappe", "exec-command.sh", "/bin/bash", "-c", f"cd /workspace/frappe-bench && bench --site {site} console"]
        else:
            exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["exec"]
            if user:
                exec_cmd += ["--user", user]
            exec_cmd += ["--workdir", "/workspace/frappe-bench"]
            exec_cmd += ["frappe", "bench", "--site", site, "console"]

        os.execvp(exec_cmd[0], exec_cmd)

    frappe_init_wrapper = f"""import sys
import os
os.chdir('/workspace/frappe-bench/sites')
sys.path.insert(0, '/workspace/frappe-bench/apps')
import frappe
frappe.init(site='{site}')
frappe.connect()

{python_code}
"""

    encoded_code = base64.b64encode(frappe_init_wrapper.encode()).decode()
    bench_console_cmd = (
        f"FM_EXEC_CODE='{encoded_code}' && echo $FM_EXEC_CODE | base64 -d | /workspace/frappe-bench/env/bin/python"
    )

    exit_code = bench.execute_command("frappe", bench_console_cmd, user, use_run=run)
    if exit_code != 0:
        raise typer.Exit(exit_code)


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
    bench_console: Annotated[
        bool,
        typer.Option(
            "--bench-console",
            help="Open bench console with Frappe context (interactive IPython or execute code via -c/stdin)",
        ),
    ] = False,
    site: Annotated[
        str | None, typer.Option(help="Site name for bench console (defaults to benchname if not specified)")
    ] = None,
):
    """
    Spawn shell for the bench or execute a command.

    Supports multiple input modes:
    - Interactive shell (no input)
    - Command execution (-c flag)
    - Heredoc/piped commands (stdin)
    - Passthrough args (-- syntax)

    The --bench-console flag provides three modes:
    - Interactive: Opens IPython console with Frappe context (no -c or piped input)
    - Script: Executes piped Python code (stdin)
    - Inline: Executes -c command directly

    In interactive mode, provides full terminal support.
    Exit code from executed commands is preserved.
    """

    check_bench_migration_required(benchname)

    assert benchname is not None

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    logger = ctx.obj.get("logger")
    bench = Bench.get_object(benchname, services_manager, logger=logger, output_handler=output)

    available_services = bench.get_available_services()
    if service not in available_services:
        output.display_error(f"Service '{service}' not found")
        output.print(f"Available services: {', '.join(sorted(available_services))}")
        raise typer.Exit(1)

    if bench_console:
        if service != "frappe":
            output.display_error("--bench-console only works with the frappe service")
            raise typer.Exit(1)

        user = _get_default_user(service, user)
        output.stop()
        _handle_bench_console(bench, benchname, command, site, user, run, output)
        return

    output.stop()

    user = _get_default_user(service, user)
    shell_path = _get_default_shell_path(service, shell_path)

    passthrough_args = ctx.args if ctx.args else None
    is_interactive = output.is_interactive()
    has_stdin_data = not sys.stdin.isatty()

    if has_stdin_data and not command and not passthrough_args:
        stdin_commands = sys.stdin.read()
        exit_code = bench.execute_command(service, stdin_commands, user, shell_path=shell_path, use_run=run)
        if exit_code != 0:
            raise typer.Exit(exit_code)
        return

    if passthrough_args:
        if run:
            exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["run", "--rm"]
            # Let entrypoint handle UID remapping via exec-command.sh wrapper
            exec_cmd += [service, "exec-command.sh", shell_path, "-c", " ".join(passthrough_args)]
        else:
            exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["exec"]
            if user:
                exec_cmd += ["--user", user]
            if service == "frappe":
                exec_cmd += ["--workdir", "/workspace/frappe-bench"]
            exec_cmd += [service, shell_path, "-c", " ".join(passthrough_args)]

        if is_interactive:
            os.execvp(exec_cmd[0], exec_cmd)
        else:
            command_str = " ".join(passthrough_args)
            exit_code = bench.execute_command(service, command_str, user, shell_path=shell_path, use_run=run)
            if exit_code != 0:
                raise typer.Exit(exit_code)
        return

    if command:
        exit_code = bench.execute_command(service, command, user, shell_path=shell_path, use_run=run)
        if exit_code != 0:
            raise typer.Exit(exit_code)
        return

    bench.shell(service, user, shell_path=shell_path, use_run=run)
