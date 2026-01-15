from datetime import datetime
import json
from pathlib import Path
import typer
from typing import List, Optional, Set
from frappe_manager.site_manager.exceptions import BenchNotFoundError
from frappe_manager.utils.helpers import check_frappe_app_exists, get_current_fm_version
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import (
    CLI_BENCHES_DIRECTORY,
    CLI_CACHE_PATH,
    CLI_RECENT_USED_SITES_CACHE_PATH,
    STABLE_APP_BRANCH_MAPPING_LIST,
    DEFAULT_EXTENSIONS,
)
from frappe_manager.utils.site import get_sitename_from_current_path, validate_sitename, is_fqdn, is_wildcard_fqdn


def apps_list_validation_callback(value: List[str] | None):
    """
    Parse and validate the list of apps provided.

    Supports formats:
    - "erpnext" → frappe/erpnext (default org)
    - "erpnext:version-15" → frappe/erpnext:version-15
    - "frappe/erpnext:version-15" → frappe/erpnext:version-15
    - "rtcamp/custom-app:main" → rtcamp/custom-app:main
    - "frappe/frappe:version-15#apps/frappe" → subdirectory app (monorepo)

    Validation is lightweight here - actual repo existence is validated
    during cloning by AppCloner with proper error messages.

    Args:
        value (List[str] | None): The list of apps to validate.

    Raises:
        typer.BadParameter: If format is invalid or 'frappe' app is included.

    Returns:
        List[str] | None: The parsed list of apps as dicts.
    """
    apps_list = []

    if value:
        for app in value:
            # Allow frappe app now - it can be specified via --apps
            # No need to check and reject frappe anymore

            # Handle HTTP/HTTPS URLs
            if "https://" in app or "http://" in app:
                appx = app.split(":")
                temp_appx = appx
                appx = [":".join(appx[:2])]

                if len(temp_appx) == 3:
                    appx.append(temp_appx[2])
                elif len(temp_appx) > 3:
                    appx.append(temp_appx[2])
            else:
                # Split on ':' for branch/ref (handle subdirectory '#' first)
                # e.g., "frappe/payments:version-15#apps/payments"
                if '#' in app:
                    # Has subdirectory - split carefully
                    app_part = app.split('#')[0]
                    appx = app_part.split(":")
                    # Reconstruct with subdirectory
                    if len(appx) == 2:
                        appx = [appx[0], app.split(':', 1)[1]]
                    else:
                        appx = [app]
                else:
                    appx = app.split(":")

            # Basic format validation
            if len(appx) > 2:
                richprint.stop()
                msg = (
                    "Specify the app in the format:\n"
                    "  <appname>:<branch>\n"
                    "  <org>/<appname>:<branch>\n"
                    "  <org>/<appname>:<branch>#<subdir>\n"
                    "\nExamples:\n"
                    "  erpnext:version-15\n"
                    "  frappe/helpdesk:v1.9.1\n"
                    "  rtcamp/custom-app:main\n"
                    "  frappe/frappe:version-15#apps/frappe"
                )
                raise typer.BadParameter(msg)

            # Build the dict format expected by downstream code
            appx_dict = {
                'app': appx[0],
                'branch': appx[1] if len(appx) > 1 else None,
            }
            apps_list.append(appx_dict)

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


def val(answers, current):
    print(answers, current)


def sitename_callback(sitename: Optional[str]):
    if not sitename:
        sitename = get_sitename_from_current_path()

    if not sitename:
        from InquirerPy import inquirer

        # Get basic sites list
        sites_list = [site_name.parent.name for site_name in sites_autocompletion_callback()]

        if sites_list:
            richprint.stop()

            # Sort with recently used sites first
            sorted_sites = get_sorted_sites_list(sites_list)

            sitename = inquirer.fuzzy(
                message="Select bench (↑↓ navigate, type to search)",
                vi_mode=True,
                choices=sorted_sites,
                mandatory=True,
                qmark='🤔',
                amark='🤔',
            ).execute()

            # Update cache with selected site
            if sitename:
                update_sites_cache(sitename)

            richprint.start("working")

    if sitename is None:
        raise typer.BadParameter("Invalid selection. Must match existing sites")

    sitename = validate_sitename(sitename)

    # check if bench not exists
    bench_path = CLI_BENCHES_DIRECTORY / sitename

    if not bench_path.exists():
        raise BenchNotFoundError(sitename, bench_path)

    return sitename


def get_cache_file() -> Path:
    """Returns the path to the cache file for recently used sites"""
    CLI_CACHE_PATH.mkdir(parents=True, exist_ok=True)
    return CLI_RECENT_USED_SITES_CACHE_PATH


def update_sites_cache(sitename: str) -> None:
    """Updates the cache with the most recently used site"""
    cache_file = get_cache_file()
    try:
        if cache_file.exists():
            with open(cache_file, "r") as f:
                cache = json.load(f)
        else:
            cache = {"sites": []}

        # Remove if exists and add to front
        cache["sites"] = [s for s in cache["sites"] if s["name"] != sitename]
        cache["sites"].insert(0, {"name": sitename, "last_used": datetime.now().isoformat()})

        # Keep only last 10 entries
        cache["sites"] = cache["sites"][:10]

        with open(cache_file, "w") as f:
            json.dump(cache, f)
    except Exception:
        # Fail silently if cache operations fail
        pass


def get_sorted_sites_list(sites_list: list[str]) -> list[str]:
    """Returns sites list with recently used sites first, but only for sites that actually exist"""
    cache_file = get_cache_file()
    try:
        if cache_file.exists():
            with open(cache_file, "r") as f:
                cache = json.load(f)

            # Get cached site names, but only if they exist in the actual sites_list
            cached_sites = [s["name"] for s in cache["sites"] if s["name"] in sites_list]

            # Get remaining sites that aren't in cache
            remaining_sites = [s for s in sites_list if s not in cached_sites]

            # Return cached sites first, then remaining sites
            return cached_sites + remaining_sites
    except Exception:
        pass

    return sites_list


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
        raise typer.BadParameter(f"The bench '{sitename}' already exists at {bench_path}. Aborting operation.")

    return sitename


def alias_domains_validation_callback(value: Optional[str]) -> List[str]:
    """
    Validate the comma-separated list of alias domains.

    Args:
        value (Optional[str]): Comma-separated list of alias domains

    Returns:
        List[str]: List of validated alias domains

    Raises:
        typer.BadParameter: If any domain is invalid
    """
    if not value:
        return []

    # Split by comma and strip whitespace
    domains = [domain.strip() for domain in value.split(',') if domain.strip()]

    if not domains:
        return []

    validated_domains = []

    for domain in domains:
        # Check if it's a wildcard domain
        if domain.startswith('*.'):
            if not is_wildcard_fqdn(domain):
                richprint.stop()
                raise typer.BadParameter(
                    f"Invalid wildcard domain '{domain}'. Wildcard domains must be in format '*.example.com'."
                )
            validated_domains.append(domain)
        else:
            # Regular domain validation
            if not is_fqdn(domain):
                richprint.stop()
                raise typer.BadParameter(
                    f"Invalid domain '{domain}'. Domain must be a valid FQDN (e.g., 'www.example.com')."
                )
            # Additional check: domain must have at least one dot (TLD)
            if '.' not in domain:
                richprint.stop()
                raise typer.BadParameter(f"Invalid domain '{domain}'. Domain must include a TLD (e.g., 'example.com').")
            validated_domains.append(domain)

    # Check for duplicates
    if len(validated_domains) != len(set(validated_domains)):
        richprint.stop()
        raise typer.BadParameter("Duplicate domains found in alias domains list.")

    return validated_domains
