import json

import requests
import typer
import typer

from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import get_current_fm_version, install_package
from frappe_manager.utils.site import pull_docker_images
from frappe_manager.commands import app

self_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
app.add_typer(self_app, name="self", help="Perform operations related to the [bold][blue]fm[/bold][/blue] itself.")

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
        richprint.exit(f"Error occured while updating the app : {e}")

@self_app.command('update-images')
def update_images(
    ctx: typer.Context,
):
    """Pull latest FM stack docker images."""

    pull_docker_images()
