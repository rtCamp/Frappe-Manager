import base64
import os
import sys
from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.commands import check_bench_migration_required
from frappe_manager.commands.arguments import BenchNameArgument
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager import NON_BASH_SUPPORTED_SERVICES
from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.site import Bench


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
            exec_cmd = bench.docker_client.compose.docker_compose_cmd + [
                "run",
                "--rm",
                "--entrypoint",
                "/exec-entrypoint.sh",
            ]
            # Use lightweight exec-entrypoint.sh that only handles UID/GID mismatch
            exec_cmd += ["frappe", "/bin/bash", "-c", f"cd /workspace/frappe-bench && bench --site {site} console"]
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


@example(
    "Open a shell in the bench",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Run one command",
    "{benchname} -- bench migrate",
    detail="Everything after -- is joined into one command line and run through the container's shell.",
    benchname="mybench",
)
@example(
    "Pipe a script in",
    "{benchname} <<'EOF'\nbench build --app frappe\nbench clear-cache\nEOF",
    detail="stdin is read as a shell script whenever it is not a terminal.",
    benchname="mybench",
)
@example(
    "Work in the Frappe context",
    "{benchname} --bench-console",
    detail="An IPython console with frappe initialised. With -c or piped input it runs Python instead.",
    benchname="mybench",
)
@example(
    "Use a throwaway container",
    "{benchname} --run -- bench migrate",
    detail="--run goes through 'docker compose run --rm' rather than the bench's own container.",
    benchname="mybench",
)
def shell(
    ctx: typer.Context,
    benchname: BenchNameArgument = None,
    command: Annotated[str | None, typer.Option("-c", "--command", help="Run this command and exit.")] = None,
    user: Annotated[
        str | None,
        typer.Option(
            help="User inside the container. Defaults to frappe on the frappe service, and is ignored with --run.",
            show_default=False,
        ),
    ] = None,
    service: Annotated[str, typer.Option(help="Container to enter.")] = "frappe",
    shell_path: Annotated[
        str | None,
        typer.Option(help="Shell to spawn. Defaults to /bin/bash, or sh on images without bash.", show_default=False),
    ] = None,
    run: Annotated[
        bool, typer.Option(help="Use a throwaway 'docker compose run --rm' container instead of the bench's.")
    ] = False,
    bench_console: Annotated[
        bool,
        typer.Option(
            "--bench-console",
            help="Enter the Frappe context on the frappe service: bench console interactively, Python from -c or stdin.",
        ),
    ] = False,
    site: Annotated[
        str | None,
        typer.Option(help="Site the bench console connects to. Defaults to the bench name.", show_default=False),
    ] = None,
):
    """
    Open a shell in one of a bench's containers, or run a command in it.

    A command can come from -c, from the arguments after --, or from stdin when stdin is not a terminal, and its exit code becomes fm's. --bench-console works on the frappe service only: interactively it is bench console, and with -c or piped input it runs Python with frappe already initialised and connected.
    """

    check_bench_migration_required(benchname)

    assert benchname is not None

    services_manager = ctx.obj["services"]
    output = get_global_output_handler()
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if bench.bench_config.runtime == BenchRuntime.image:
        output.warning(
            "Image-mode shell is ephemeral: the app image has no workspace mount, "
            "so edits made here do not persist and are lost on the next switch/deploy. "
            "Use 'fm deploy' to ship code changes.",
        )

    available_services = bench.get_available_services()
    if service not in available_services:
        output.display_error(f"Service '{service}' not found")
        output.print(f"Available services: {', '.join(sorted(available_services))}")
        raise typer.Exit(1)

    # `--run` always goes through /exec-entrypoint.sh, which gosu-drops to the
    # bench's USERID:USERGROUP no matter which user the container started as, so
    # `--user` cannot be honoured on this path. Say so instead of dropping it in
    # silence. (Forwarding it would be worse than useless: the default user for
    # the frappe service is `frappe`, and a non-root `docker compose run --user`
    # makes the entrypoint's gosu fail outright.)
    if run and user:
        output.warning(
            f"--user {user} is ignored with --run: the run entrypoint always drops to the bench's "
            "host UID. Drop --run to choose the user."
        )

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
            exec_cmd = bench.docker_client.compose.docker_compose_cmd + [
                "run",
                "--rm",
                "--entrypoint",
                "/exec-entrypoint.sh",
            ]
            # Use lightweight exec-entrypoint.sh that only handles UID/GID mismatch.
            # It never cds, and the stock image's WORKDIR is /workspace (one level
            # above the bench), so `bench ...` needs the same --workdir exec gets.
            if service == "frappe":
                exec_cmd += ["--workdir", "/workspace/frappe-bench"]
            exec_cmd += [service, shell_path, "-c", " ".join(passthrough_args)]
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
