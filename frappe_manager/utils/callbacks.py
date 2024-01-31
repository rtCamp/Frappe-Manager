import typer
from typing import List, Optional
from frappe_manager.utils.helpers import check_frappe_app_exists, get_current_fm_version
from frappe_manager.display_manager.DisplayManager import richprint


def apps_list_validation_callback(value: List[str] | None):
    """
    Validate the list of apps provided.

    Args:
        value (List[str] | None): The list of apps to validate.

    Raises:
        typer.BadParameter: If the list contains the 'frappe' app, or if any app is invalid or has an invalid branch.

    Returns:
        List[str] | None: The validated list of apps.
    """
    if value:
        for app in value:
            appx = app.split(":")
            if appx == "frappe":
                raise typer.BadParameter("Frappe should not be included here.")
            if len(appx) == 1:
                exists = check_frappe_app_exists(appx[0])
                if not exists["app"]:
                    raise typer.BadParameter(f"{app} is not a valid FrappeVerse app!")
            if len(appx) == 2:
                exists = check_frappe_app_exists(appx[0], appx[1])
                if not exists["app"]:
                    raise typer.BadParameter(f"{app} is not a valid FrappeVerse app!")
                if not exists["branch"]:
                    raise typer.BadParameter(
                        f"{appx[1]} is not a valid branch of {appx[0]}!"
                    )
            if len(appx) > 2:
                raise typer.BadParameter(
                    "App should be specified in format <appname>:<branch> or <appname>"
                )
    return value


def frappe_branch_validation_callback(value: str):
    """
    Validate the given Frappe branch.

    Args:
        value (str): The Frappe branch to validate.

    Returns:
        str: The validated Frappe branch.

    Raises:
        typer.BadParameter: If the Frappe branch is not valid.
    """
    if value:
        exists = check_frappe_app_exists("frappe", value)
        if exists['branch']:
            return value
        else:
            raise typer.BadParameter(f"Frappe branch -> {value} is not valid!! ")

def version_callback(version: Optional[bool] = None):
    """
    Callback function to handle version option.

    Args:
        version (bool, optional): If True, prints the current FM version and exits. Defaults to None.
    """
    if version:
        fm_version = get_current_fm_version()
        richprint.print(fm_version, emoji_code='')
        raise typer.Exit()