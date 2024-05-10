from frappe_manager.utils.site import pull_docker_images
import typer
import requests
import json
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import get_current_fm_version, install_package

update_app = typer.Typer(rich_markup_mode="rich")


@update_app.callback(invoke_without_command=True)
def update_callback(
    ctx: typer.Context,
):
    if ctx.invoked_subcommand == None:
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


@update_app.command()
def images(
    ctx: typer.Context,
):
    """Pull latest FM stack docker images."""

    pull_docker_images()
