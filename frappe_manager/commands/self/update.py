import json
import typer
import requests
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.helpers import get_current_fm_version, install_package


def update(ctx: typer.Context):
    """Check for and install frappe-manager updates."""
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
