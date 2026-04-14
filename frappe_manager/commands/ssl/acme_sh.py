"""acme.sh passthrough command."""

import os

import typer
from typer_examples import example

from frappe_manager.utils.subprocess import stream_command_output

from .helpers import get_output_handler


@example(
    "Show acme.sh help and available commands",
    "",
    detail="Displays acme.sh help. Use for learning available subcommands and flags.",
)
@example(
    "List all certificates managed by acme.sh",
    "--list",
    detail="Lists certificates that acme.sh currently manages in its home directory.",
)
@example(
    "Show certificate information for a domain",
    "--info -d example.com",
    detail="Shows detailed information for a managed certificate for the domain.",
)
@example(
    "Check acme.sh version",
    "--version",
    detail="Prints the installed acme.sh version used by FM.",
)
@example(
    "Upgrade acme.sh to latest version",
    "--upgrade",
    detail="Upgrades the bundled acme.sh installation to the latest release.",
)
@example(
    "Force renew certificate for a domain",
    "--renew -d example.com --force",
    detail="Forces a renewal for a certificate using acme.sh; advanced option for recovery and testing.",
)
def acmesh_passthrough(
    ctx: typer.Context,
):
    """
    Run acme.sh commands directly with FM's environment (advanced users).

    Advanced users only: this bypasses FM's certificate management. Prefer 'fm ssl add/remove/renew' for normal workflows.
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
