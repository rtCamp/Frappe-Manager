import json
from typing import Annotated

import requests
import typer

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.helpers import get_current_fm_version, install_package


def update(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt and proceed with update")] = False,
):
    """Check for and install frappe-manager updates."""
    output = get_global_output_handler()
    output.change_head("Checking for udpates")
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
            continue_update = output.prompt_ask(
                prompt=update_msg, choices=["yes", "no"], force_yes=yes, required_flag="--yes",
            )

            if continue_update == "yes":
                install_package("frappe-manager", latest_version)
    except Exception as e:
        output = get_global_output_handler()
        output.error(f"Error occurred while updating the app: {e}", exception=e)
        raise typer.Exit(1)
