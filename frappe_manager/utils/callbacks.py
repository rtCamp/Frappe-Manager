import json
from datetime import datetime
from pathlib import Path

import typer

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    CLI_CACHE_PATH,
    CLI_RECENT_USED_SITES_CACHE_PATH,
    DEFAULT_EXTENSIONS,
)
from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.exceptions import BenchNotFoundError
from frappe_manager.utils.address import Address, parse_address
from frappe_manager.utils.helpers import check_frappe_app_exists, get_current_fm_version
from frappe_manager.utils.site import get_sitename_from_current_path, is_fqdn, is_wildcard_fqdn, validate_sitename


def apps_list_validation_callback(value: list[str] | None):
    """
    Parse and validate the list of apps provided, returning AppConfig objects.

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
        List[AppConfig] | None: The parsed list of apps as AppConfig objects.
    """
    from frappe_manager.site_manager.bench_config import AppConfig

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

                if len(temp_appx) == 3 or len(temp_appx) > 3:
                    appx.append(temp_appx[2])
            # Split on ':' for branch/ref (handle subdirectory '#' first)
            # e.g., "frappe/payments:version-15#apps/payments"
            elif "#" in app:
                # Has subdirectory - split carefully
                app_part = app.split("#")[0]
                appx = app_part.split(":")
                # Reconstruct with subdirectory
                if len(appx) == 2:
                    appx = [appx[0], app.split(":", 1)[1]]
                else:
                    appx = [app]
            else:
                appx = app.split(":")

            # Basic format validation
            if len(appx) > 2:
                output = get_global_output_handler()
                output.stop()
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

            app_config = AppConfig.from_string(app)
            apps_list.append(app_config)

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
        raise typer.BadParameter(f"Frappe branch -> {value} is not valid!! ")


def version_callback(version: bool | None = None):
    """
    Callback function to handle version option.

    Args:
        version (bool, optional): If True, prints the current FM version and exits. Defaults to None.
    """
    if version:
        fm_version = get_current_fm_version()
        output = get_global_output_handler()
        output.print(fm_version, emoji_code="")
        raise typer.Exit()


def sites_autocompletion_callback() -> list[Path]:
    sites_list = []
    for dir in CLI_BENCHES_DIRECTORY.iterdir():
        if dir.is_dir():
            dir = dir / "docker-compose.yml"
            if dir.exists() and dir.is_file():
                sites_list.append(dir)
    return sites_list


RESERVED_BENCH_NAME = "all"
"""Refused as a bench name: a bare `all` is the address meaning every bench."""

_SITE_PART_REFUSAL = "this command takes a bench, not a site: use '{bench}'"


def _parse_or_refuse(value: str) -> Address:
    """Parse an address, turning a parse failure into the CLI's own refusal.

    `parse_address` is pure and raises `ValueError`; the CLI layer owns how a refusal
    reaches the operator, so it becomes `typer.BadParameter` here and nowhere deeper.
    """
    try:
        return parse_address(value)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def _resolve_bench(sitename: str | None) -> str:
    """A bench name: the CWD fallback, then the picker, then normalise and require it exists."""
    if not sitename:
        sitename = get_sitename_from_current_path()

    if not sitename:
        sites_list = [site_name.parent.name for site_name in sites_autocompletion_callback()]

        if sites_list:
            output = get_global_output_handler()
            sorted_sites = get_sorted_sites_list(sites_list)

            try:
                sitename = output.prompt_fuzzy(
                    prompt="Select bench (↑↓ navigate, type to search)",
                    choices=sorted_sites,
                    vi_mode=True,
                    mandatory=True,
                    qmark="🤔",
                    amark="🤔",
                )

                if sitename:
                    update_sites_cache(sitename)
            except Exception as e:
                raise NonInteractiveError(
                    "Bench name is required in non-interactive mode",
                    suggestions=[
                        "Specify the bench name as a positional argument",
                        "Run 'fm list' to see available benches",
                    ],
                ) from e

    if sitename is None:
        raise typer.BadParameter("Invalid selection. Must match existing sites")

    # A bench name is a name, not a domain: it is taken as typed. `validate_sitename` still runs,
    # because the name has to be a legal DNS label to serve as a directory and a compose prefix,
    # but its `.localhost` form is only used as the LEGACY fallback below.
    fqdn = validate_sitename(sitename)

    bench_path = CLI_BENCHES_DIRECTORY / sitename

    if not bench_path.exists():
        # Benches created before the names came apart are named `shop.localhost`, so `fm start shop`
        # has to keep finding one. Tried second, so a bench genuinely called `shop` always wins and
        # the fallback can never shadow it.
        legacy_path = CLI_BENCHES_DIRECTORY / fqdn
        if legacy_path != bench_path and legacy_path.exists():
            return fqdn
        raise BenchNotFoundError(sitename, bench_path)

    return sitename


def sitename_callback(sitename: str | None):
    """`BENCH` for every command that acts on a whole bench.

    An address carrying a site part is refused rather than ignored: containers, the
    workspace and the workers are shared by every site in a bench, so there is no
    per-site meaning to invent for these commands.
    """
    if sitename:
        address = _parse_or_refuse(sitename)
        if address.site is not None:
            raise typer.BadParameter(_SITE_PART_REFUSAL.format(bench=address.bench))
        sitename = address.bench

    return _resolve_bench(sitename)


def _recorded_sites(bench: str) -> list[str]:
    """The sites a bench serves, from its `[sites]` table.

    Falls back to `[bench]` when the config is absent, unreadable or records nothing. That is not a
    compatibility branch for an old shape: this runs in a parameter callback, so it must not be the
    thing that turns a broken config into a stack trace before the command body gets a chance to
    report it properly. A bench whose config cannot be read will fail again, and better, in the body.
    """
    # Deferred: bench_config pulls in the site_manager package, which imports this module.
    from frappe_manager.site_manager.bench_config import BenchConfig

    config_path = CLI_BENCHES_DIRECTORY / bench / CLI_BENCH_CONFIG_FILE_NAME
    if not config_path.is_file():
        return [bench]
    try:
        recorded = BenchConfig.import_from_toml(config_path).sites
    except Exception:
        return [bench]
    return list(recorded) if recorded else [bench]


def bench_site_callback(ctx: typer.Context, value: str | None) -> str | None:
    """`BENCH[/SITE]` for the one command that addresses a site.

    Returns the BENCH name, exactly as `sitename_callback` does, so command bodies keep
    receiving a plain bench-directory name and the 21 `Bench.get_object` call sites are
    untouched. A named site rides on `ctx.obj["site"]` instead.

    `ctx` must be annotated: typer matches the context parameter by annotation and then
    takes the last un-annotated parameter as the value.
    """
    site = None

    if value:
        address = _parse_or_refuse(value)
        value = address.bench
        site = address.site

    bench = _resolve_bench(value)

    if site is not None:
        site = validate_sitename(site)
        recorded = _recorded_sites(bench)
        if site not in recorded:
            known = ", ".join(f"'{s}'" for s in sorted(recorded))
            raise typer.BadParameter(
                f"bench '{bench}' has no site '{site}'. It serves {known}."
            )
        # `ctx.obj` is None under --help, which short-circuits before app_callback fills it.
        if ctx.obj is not None:
            ctx.obj["site"] = site

    return bench


def standalone_address_callback(value: str | None) -> str | None:
    """`BENCH` for the `ssl` commands, which resolve the bench themselves.

    Parses and refuses a site part, then returns the value otherwise UNCHANGED: no
    normalisation and no must-exist check, because these commands also manage
    certificates for domains that belong to no bench. Without this, a slashed value
    reached `Bench.get_object` and died as a not-found error on a nested path.
    """
    if not value:
        return value

    address = _parse_or_refuse(value)
    if address.site is not None:
        raise typer.BadParameter(_SITE_PART_REFUSAL.format(bench=address.bench))

    return address.bench


def get_cache_file() -> Path:
    """Returns the path to the cache file for recently used sites"""
    CLI_CACHE_PATH.mkdir(parents=True, exist_ok=True)
    return CLI_RECENT_USED_SITES_CACHE_PATH


def update_sites_cache(sitename: str) -> None:
    """Updates the cache with the most recently used site"""
    cache_file = get_cache_file()
    try:
        if cache_file.exists():
            with open(cache_file) as f:
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
            with open(cache_file) as f:
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


def prompt_for_bench_selection(current_value: str | None) -> str | None:
    if current_value:
        return current_value

    from frappe_manager.output_manager import get_global_output_handler

    benchname = get_sitename_from_current_path()
    if benchname:
        return benchname

    sites_list = [site_name.parent.name for site_name in sites_autocompletion_callback()]

    if not sites_list:
        return None

    output = get_global_output_handler()
    sorted_sites = get_sorted_sites_list(sites_list)

    try:
        selected = output.prompt_fuzzy(
            prompt="Select bench (↑↓ navigate, type to search)",
            choices=sorted_sites,
            vi_mode=True,
            mandatory=True,
            qmark="🤔",
            amark="🤔",
        )

        if selected:
            update_sites_cache(selected)
            return selected
    except Exception:
        # Silently fail - caller will handle None return
        pass

    return None


def code_command_extensions_callback(extensions: list[str]) -> list[str]:
    extx = extensions + DEFAULT_EXTENSIONS
    unique_ext: set = set(extx)
    unique_ext_list: list[str] = [x for x in unique_ext]
    return unique_ext_list


def create_command_sitename_callback(sitename: str):
    address = _parse_or_refuse(sitename)

    if address.site is not None:
        # `fm create` cannot add a site to a bench that does not exist yet, so a site part
        # here is always a mistake.
        raise typer.BadParameter("fm create takes a bench name, not a bench/site address")

    if address.bench == RESERVED_BENCH_NAME:
        # Checked before validate_sitename, which would turn it into `all.localhost`.
        raise typer.BadParameter(
            f"'{RESERVED_BENCH_NAME}' is reserved as an address meaning every bench, so it cannot be a bench name"
        )

    # Validate the name WITHOUT taking the `.localhost` form it returns. A bench name is just a
    # name from here on: `fm create shop` makes a bench called `shop`, and the site it serves is
    # `shop.localhost`, minted from this same name inside the command. `validate_sitename` is still
    # the right validator, because a bench name has to be a legal DNS label either way: it becomes
    # a directory name and a compose project prefix.
    _ = validate_sitename(address.bench)
    benchname = address.bench

    # check if already exists
    bench_path = CLI_BENCHES_DIRECTORY / benchname

    if bench_path.exists():
        raise typer.BadParameter(f"The bench '{benchname}' already exists at {bench_path}. Aborting operation.")

    # A bench created before the names came apart is named `shop.localhost`, so `fm create shop`
    # would otherwise happily make a second bench beside it serving the same site.
    legacy_path = CLI_BENCHES_DIRECTORY / validate_sitename(address.bench)
    if legacy_path != bench_path and legacy_path.exists():
        raise typer.BadParameter(
            f"The bench '{legacy_path.name}' already exists at {legacy_path} and serves the site "
            f"'{legacy_path.name}', which is the site '{benchname}' would serve. Aborting operation."
        )

    return benchname


def alias_domains_validation_callback(value: str | None) -> list[str]:
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
    domains = [domain.strip() for domain in value.split(",") if domain.strip()]

    if not domains:
        return []

    validated_domains = []

    for domain in domains:
        # Check if it's a wildcard domain
        if domain.startswith("*."):
            if not is_wildcard_fqdn(domain):
                output = get_global_output_handler()
                output.stop()
                raise typer.BadParameter(
                    f"Invalid wildcard domain '{domain}'. Wildcard domains must be in format '*.example.com'.",
                )
            validated_domains.append(domain)
        else:
            # Regular domain validation
            if not is_fqdn(domain):
                output = get_global_output_handler()
                output.stop()
                raise typer.BadParameter(
                    f"Invalid domain '{domain}'. Domain must be a valid FQDN (e.g., 'www.example.com').",
                )
            # Additional check: domain must have at least one dot (TLD)
            if "." not in domain:
                output = get_global_output_handler()
                output.stop()
                raise typer.BadParameter(f"Invalid domain '{domain}'. Domain must include a TLD (e.g., 'example.com').")
            validated_domains.append(domain)

    # Check for duplicates
    if len(validated_domains) != len(set(validated_domains)):
        output = get_global_output_handler()
        output.stop()
        raise typer.BadParameter("Duplicate domains found in alias domains list.")

    return validated_domains
