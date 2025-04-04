from pathlib import Path
from typing import Optional
import typer
from typing import List, Optional, Set
from frappe_manager.site_manager.site_exceptions import BenchNotFoundError
from frappe_manager.utils.helpers import check_frappe_app_exists, get_current_fm_version
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import CLI_BENCHES_DIRECTORY, STABLE_APP_BRANCH_MAPPING_LIST, DEFAULT_EXTENSIONS
from frappe_manager.utils.site import get_sitename_from_current_path, validate_sitename


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
                msg = (
                    "Specify the app in the format <appname>:<branch> or <appname>."
                    "\n<appname> can be a URL or, if it's a FrappeVerse app, simply provide it as 'erpnext' or 'hrms:develop'."
                )
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

            appx = {
                'app': appx[0],
                'branch': appx[1] if len(appx) > 1 else None,
            }
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


def sites_autocompletion_callback() -> list[Path]:
    sites_list = []
    for dir in CLI_BENCHES_DIRECTORY.iterdir():
        if dir.is_dir():
            dir = dir / "docker-compose.yml"
            if dir.exists() and dir.is_file():
                sites_list.append(dir)
    return sites_list


def sitename_callback(sitename: Optional[str]):
    if not sitename:
        sitename = get_sitename_from_current_path()

    if not sitename:
        richprint.stop()
        from InquirerPy import inquirer
        sites_list = [site_name.parent.name for site_name in sites_autocompletion_callback()]
        sitename = inquirer.fuzzy(
            message="Please select a bench (use arrows/type to search)",
            vi_mode=True,
            choices=sites_list
        ).execute()
        richprint.start("working")

    sitename = validate_sitename(sitename)

    # check if bench not exists
    bench_path = CLI_BENCHES_DIRECTORY / sitename

    if not bench_path.exists():
        raise BenchNotFoundError(sitename, bench_path)

    return sitename


def code_command_extensions_callback(extensions: List[str]) -> List[str]:
    extx = extensions + DEFAULT_EXTENSIONS
    unique_ext: Set = set(extx)
    unique_ext_list: List[str] = [x for x in unique_ext]
    return unique_ext_list


def create_command_sitename_callback(sitename: str):
    # validate the site
    sitename = validate_sitename(sitename)

    # check if already exists
    bench_path = CLI_BENCHES_DIRECTORY / sitename

    if bench_path.exists():
        richprint.exit(f"The bench '{sitename}' already exists at {bench_path}. Aborting operation.")

    return sitename
