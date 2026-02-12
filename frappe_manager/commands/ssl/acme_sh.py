"""acme.sh passthrough command."""

import os

import typer

from frappe_manager.utils.subprocess import stream_command_output

from .helpers import get_output_handler


def acmesh_passthrough(
    ctx: typer.Context,
):
    """
    Run acme.sh commands directly with FM's environment (advanced users).

    [bold yellow]⚠️  Advanced users only![/bold yellow]
    This bypasses FM's certificate management. Use 'fm ssl add/remove/renew' for normal operations.

    This command provides direct access to acme.sh for advanced operations like certificate
    listing, info checking, manual renewals, revocations, and debugging.

    All acme.sh commands run with FM's SSL directory configuration and full access to
    certificate storage.
    """
    args = ctx.args

    services_manager = ctx.obj["services"]
    output = get_output_handler(ctx)

    global_proxy_storage = services_manager.proxy_storage
    ssl_dir = global_proxy_storage.dirs.ssl.host
    acmesh_home = ssl_dir / "acmesh" / ".acme.sh"
    acmesh_bin = acmesh_home / "acme.sh"

    if not acmesh_bin.exists():
        output.display_error("acme.sh is not installed yet")
        output.info("Run 'fm ssl add <benchname> <domain>' to install acme.sh first")
        raise typer.Exit(1)

    cmd = [str(acmesh_bin), "--home", str(acmesh_home)]

    # If no args provided, show help
    if not args:
        cmd.append("--help")
    else:
        cmd.extend(args)

    env = os.environ.copy()
    env["LE_WORKING_DIR"] = str(acmesh_home)

    output.change_head("Running acme.sh")
    output.info(f"Command: acme.sh {' '.join(args or ['--help'])}")
    output.info(f"Home: {acmesh_home}")
    output.print("")

    # Stream output directly to user
    exit_code_holder = [0]

    def stream_with_exit_tracking():
        """Generator that tracks exit code while yielding output."""
        for source, line in stream_command_output(cmd, env=env, cwd=None):
            if source == "exit_code":
                exit_code_holder[0] = int(line.decode())
            yield source, line

    # Display all output (print directly for raw acme.sh output)
    for source, line in stream_with_exit_tracking():
        if source in ("stdout", "stderr"):
            # Print directly without prefix for raw acme.sh output
            decoded = line.decode()
            print(decoded, flush=True)

    # Exit with acme.sh's exit code
    if exit_code_holder[0] != 0:
        output.print("")
        output.display_error(f"acme.sh exited with code {exit_code_holder[0]}")
        raise typer.Exit(exit_code_holder[0])
    output.print("")
    output.print("Command completed successfully", emoji_code=":white_check_mark:")
