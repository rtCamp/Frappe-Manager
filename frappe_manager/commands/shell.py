import sys
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
    bench_console: Annotated[
        bool,
        typer.Option(
            "--bench-console",
            help="Open bench console with Frappe context (interactive IPython or execute code via -c/stdin)",
        ),
    ] = False,
    site: Annotated[
        str | None, typer.Option(help="Site name for bench console (auto-detected if not specified)")
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

    if bench_console:
        if service != "frappe":
            output.display_error("--bench-console only works with the frappe service")
            raise typer.Exit(1)

        if not user:
            user = "frappe"

        output.stop()

        python_code = None

        if command:
            python_code = command
        elif not sys.stdin.isatty():
            python_code = sys.stdin.read()
        else:
            if not site:
                get_current_site_cmd = "cat /workspace/frappe-bench/sites/currentsite.txt 2>/dev/null || ls -1 /workspace/frappe-bench/sites/ | grep -v '^\\.\\|^apps.txt$\\|^assets$\\|^common_site_config.json$\\|currentsite.txt' | head -1"
                result = bench.docker_client.compose.exec(
                    service="frappe",
                    command=f'/bin/bash -c "{get_current_site_cmd}"',
                    stream=False,
                    capture_output=True,
                )

                if result.stdout and result.stdout[0].strip():
                    site = result.stdout[0].strip()
                    output.print(f"Using site: {site}")
                else:
                    output.display_error("Could not detect site. Please specify --site option")
                    output.print("Example: fm shell mybench --bench-console --site mysite.localhost")
                    raise typer.Exit(1)

            import os

            if run:
                exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["run", "--rm"]
                if user:
                    exec_cmd += ["--user", user]
                exec_cmd += ["--entrypoint", "/bin/bash"]
                exec_cmd += ["frappe", "-c", f"cd /workspace/frappe-bench && bench --site {site} console"]
            else:
                exec_cmd = bench.docker_client.compose.docker_compose_cmd + ["exec"]
                if user:
                    exec_cmd += ["--user", user]
                exec_cmd += ["--workdir", "/workspace/frappe-bench"]
                exec_cmd += ["frappe", "bench", "--site", site, "console"]

            os.execvp(exec_cmd[0], exec_cmd)

        import base64

        if not site:
            get_current_site_cmd = "cat /workspace/frappe-bench/sites/currentsite.txt 2>/dev/null || ls -1 /workspace/frappe-bench/sites/ | grep -v '^\\.\\|^apps.txt$\\|^assets$\\|^common_site_config.json$\\|currentsite.txt' | head -1"
            result = bench.docker_client.compose.exec(
                service="frappe",
                command=f'/bin/bash -c "{get_current_site_cmd}"',
                stream=False,
                capture_output=True,
            )

            if result.stdout and result.stdout[0].strip():
                site = result.stdout[0].strip()
                output.print(f"Using site: {site}")
            else:
                output.display_error("Could not detect site. Please specify --site option")
                output.print("Example: fm shell mybench --bench-console --site mysite.localhost")
                raise typer.Exit(1)

        frappe_init_wrapper = f"""
import sys
import os

os.chdir('/workspace/frappe-bench/sites')
sys.path.insert(0, '/workspace/frappe-bench/apps')

import frappe
frappe.init(site={repr(site)})
frappe.connect()

{python_code}
"""

        frappe_init_wrapper = f"""import sys
import os
os.chdir('/workspace/frappe-bench/sites')
sys.path.insert(0, '/workspace/frappe-bench/apps')
import frappe
frappe.init(site='{site}')
frappe.connect()

{python_code}
"""

        import base64

        encoded_code = base64.b64encode(frappe_init_wrapper.encode()).decode()

        bench_console_cmd = (
            f"FM_EXEC_CODE='{encoded_code}' && echo $FM_EXEC_CODE | base64 -d | /workspace/frappe-bench/env/bin/python"
        )

        exit_code = bench.execute_command(service, bench_console_cmd, user, use_run=run)

        if exit_code != 0:
            raise typer.Exit(exit_code)

        return

    output.stop()

    passthrough_args = ctx.args if ctx.args else None
    is_interactive = output.is_interactive()
    has_stdin_data = not sys.stdin.isatty()

    if command or passthrough_args or has_stdin_data:
        if has_stdin_data and not command and not passthrough_args:
            stdin_commands = sys.stdin.read()
            if service == "frappe" and not user:
                user = "frappe"

            if not shell_path:
                non_bash_supported = ["redis-cache", "redis-queue", "adminer", "mailpit"]
                shell_path = "/bin/bash" if service not in non_bash_supported else "sh"

            exit_code = bench.execute_command(service, stdin_commands, user, shell_path=shell_path, use_run=run)

            if exit_code != 0:
                raise typer.Exit(exit_code)
        elif passthrough_args:
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
