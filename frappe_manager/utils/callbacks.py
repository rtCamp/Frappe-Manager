import typer
from pathlib import Path
from typing import List, Optional, Set
from frappe_manager.site_manager.SiteManager import SiteManager
from frappe_manager.utils.helpers import check_frappe_app_exists, get_current_fm_version, get_sitename_from_current_path
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import CLI_SITES_DIRECTORY, STABLE_APP_BRANCH_MAPPING_LIST, DEFAULT_EXTENSIONS


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
    apps_list = []

    if value:
        for app in value:
            appx = app.split(":")

            if appx == "frappe":
                raise typer.BadParameter("'frappe' should not be included here.")

            if "https:" in app or "http:" in app:
                temp_appx = appx
                appx = [":".join(appx[:2])]

                if len(temp_appx) == 3:
                    appx.append(temp_appx[2])

                elif len(temp_appx) > 3:
                    appx.append(temp_appx[2])
                    appx.append(temp_appx[2])

            if len(appx) > 2:
                richprint.stop()
                msg = "Specify the app in the format <appname>:<branch> or <appname>." "\n<appname> can be a URL or, if it's a FrappeVerse app, simply provide it as 'erpnext' or 'hrms:develop'."
                raise typer.BadParameter(msg)

            if len(appx) == 1:
                exists = check_frappe_app_exists(appx[0])

                if not exists["app"]:
                    richprint.stop()
                    raise typer.BadParameter(f"Invalid app '{appx[0]}'.")

                if appx[0] in STABLE_APP_BRANCH_MAPPING_LIST:
                    appx.append(STABLE_APP_BRANCH_MAPPING_LIST[appx[0]])

            if len(appx) == 2:
                exists = check_frappe_app_exists(appx[0], appx[1])

                if not exists["app"]:
                    richprint.stop()
                    raise typer.BadParameter(f"Invalid app '{appx[0]}'.")

                if not exists["branch"]:
                    richprint.stop()
                    raise typer.BadParameter(f"Invaid branch '{appx[1]}' for '{appx[0]}'.")

            appx = ":".join(appx)
            apps_list.append(appx)
    return apps_list


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
        if exists["branch"]:
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
        richprint.print(fm_version, emoji_code="")
        raise typer.Exit()


def sites_autocompletion_callback():
    sites = SiteManager(CLI_SITES_DIRECTORY)
    sites_list = sites.get_all_sites()
    return sites_list


def sitename_callback(sitename):
    if not sitename:
        sitename = get_sitename_from_current_path()

    if not sitename:
        raise typer.BadParameter(message="Missing Argument")

    return sitename


def code_command_extensions_callback(extensions: List[str]) -> List[str]:
    extx = extensions + DEFAULT_EXTENSIONS
    unique_ext: Set = set(extx)
    unique_ext_list: List[str] = [x for x in unique_ext]
    return unique_ext_list
