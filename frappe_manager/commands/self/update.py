import json
from typing import Annotated

import requests
import typer
from typer_examples import example

from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.helpers import get_current_fm_version, install_package


@example(
    "Update fm to the latest release",
    "",
)
@example(
    "Update without the confirmation prompt",
    "--yes",
)
def update(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Update without asking for confirmation.")] = False,
):
    """Update fm to the latest release published on PyPI.

    An install already ahead of PyPI, such as a dev or pre-release build, is reported as up to date and left alone: fm is never downgraded under benches whose on-disk state a newer fm wrote.
    """
    output = get_global_output_handler()
    output.change_head("Checking for updates")
    url = "https://pypi.org/pypi/frappe-manager/json"
    try:
        update_info = requests.get(url, timeout=2)
        update_info = json.loads(update_info.text)
        fm_version = get_current_fm_version()
        latest_version = update_info["info"]["version"]
        # Ordered comparison, not string inequality: a dev/pre-release build is AHEAD of the
        # published release, and offering the PyPI version there is a DOWNGRADE of the CLI
        # underneath benches whose on-disk state was written by the newer fm.
        if Version(latest_version) > Version(fm_version):
            update_msg = (
                f":arrows_counterclockwise: New update available [fm.accent]v{latest_version}[/fm.accent]"
                "\nDo you want to update ?"
            )
            continue_update = output.prompt_ask(
                prompt=update_msg,
                choices=["yes", "no"],
                force_yes=yes,
                required_flag="--yes",
            )

            if continue_update == "yes":
                install_package("frappe-manager", latest_version)
        else:
            output.print(f"fm is already up to date (v{fm_version})")
    except Exception as e:
        output = get_global_output_handler()
        output.error(f"Error occurred while updating the app: {e}", exception=e)
        raise typer.Exit(1)
