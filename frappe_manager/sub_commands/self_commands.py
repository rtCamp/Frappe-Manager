import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import requests
import typer

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.exceptions import OperationAborted
from frappe_manager.output_manager import spinner
from frappe_manager.utils.callbacks import sitename_callback
from frappe_manager.utils.helpers import get_current_fm_version, install_package
from frappe_manager.utils.site import pull_docker_images

self_app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)


@self_app.command()
def update(ctx: typer.Context):
    richprint.change_head("Checking for udpates")
    url = "https://pypi.org/pypi/frappe-manager/json"
    try:
        update_info = requests.get(url, timeout=2)
        update_info = json.loads(update_info.text)
        fm_version = get_current_fm_version()
        latest_version = update_info["info"]["version"]
        if not fm_version == latest_version:
            update_msg = (
                f":arrows_counterclockwise: New update available [blue][bold]v{latest_version}[/bold][/blue]"
                "\nDo you want to update ?"
            )
            continue_update = richprint.prompt_ask(prompt=update_msg, choices=["yes", "no"])

            if continue_update == 'yes':
                install_package("frappe-manager", latest_version)
    except Exception as e:
        richprint.error(f"Error occurred while updating the app: {e}")
        raise typer.Exit(1)


@self_app.command('update-images')
def update_images(
    ctx: typer.Context,
):
    """Pull latest FM stack docker images."""

    with spinner(richprint, "Pulling latest Docker images"):
        pull_docker_images()


@self_app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def compose(
    ctx: typer.Context,
    benchname: str = typer.Argument(..., help="Name of the bench"),
):
    """
    Run docker compose commands with auto-detected compose files.

    Automatically finds and includes all docker-compose*.yml files in the bench directory.
    """
    bench_name = sitename_callback(benchname)
    bench_path = CLI_BENCHES_DIRECTORY / bench_name

    compose_files = sorted(bench_path.glob("docker-compose*.yml"))

    if not compose_files:
        richprint.error(f"No docker-compose files found in {bench_path}")
        raise typer.Exit(1)

    compose_cmd = ["docker", "compose"]

    for compose_file in compose_files:
        compose_cmd.extend(["-f", compose_file.name])

    if ctx.args:
        compose_cmd.extend(ctx.args)

    richprint.change_head(f"Running docker compose {' '.join(ctx.args or [])}")

    try:
        result = subprocess.run(compose_cmd, cwd=bench_path, check=False)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        richprint.warning("Command interrupted")
        sys.exit(130)
    except Exception as e:
        richprint.error(f"Failed to run docker compose: {e}")
        raise typer.Exit(1)
