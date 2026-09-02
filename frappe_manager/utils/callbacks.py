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
from frappe_manager.utils.address import SEPARATOR, Address, parse_address
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


def _bench_names() -> list[str]:
    """Every bench fm can act on: a directory under the benches root carrying a bench config.

    The config is the test, not the directory and not the compose file, because the benches root
    also collects half-created and hand-made directories that no command could do anything with,
    and because every command reads the config before it touches a container. One registry, so
    completion, the interactive picker and `all` can never disagree about which benches exist.

    `fm list` deliberately enumerates more widely: it is a diagnostic view whose job includes
    showing a bench whose config is broken. That is a different question from this one.
    """
    try:
        entries = sorted(CLI_BENCHES_DIRECTORY.iterdir())
    except OSError:
        # No benches root yet, i.e. nothing has been created. Not an error to report from a
        # completion or a picker: there is simply nothing to offer.
        return []
    return [entry.name for entry in entries if (entry / CLI_BENCH_CONFIG_FILE_NAME).is_file()]


def resolve_bench_targets(value: str | None) -> list[str]:
    """The benches an address selects: every one for `all`, otherwise just the one named.

    One registry for every `all`, and it is the same `_bench_names` the picker and completion
    offer, so what the shell suggests is what `all` acts on.

    A directory with a compose file but an unreadable config IS included, deliberately. Excluding
    it would make `fm ssl renew all` quietly skip a bench nobody renewed and report success; the
    caller reports the failure per bench instead. That is also why this does not read any config:
    deciding membership on a file that might not parse would turn a broken bench into an absent
    one.
    """
    if value == RESERVED_BENCH_NAME:
        return _bench_names()
    return [value] if value else []


def bench_all_autocompletion_callback(incomplete: str = "") -> list[str]:
    """Shell completion for the arguments that also take `all`.

    `all` is offered alongside the bench names rather than left for the operator to remember,
    because a reserved word nothing completes reads like a word that does not exist.
    """
    return [name for name in [*_bench_names(), RESERVED_BENCH_NAME] if name.startswith(incomplete)]


def sites_autocompletion_callback(incomplete: str = "") -> list[str]:
    """Shell completion for the arguments that take a BENCH and refuse a site part.

    Bench names only. An argument whose callback rejects `BENCH/SITE` must never be offered
    one, or the shell completes the operator straight into a refusal.

    typer hands the word being completed to any completion callback that declares a `str`
    parameter for it (or one named `incomplete`), so the parameter is the whole contract; the
    default keeps the function callable in-process, where the pickers below use it.
    """
    incomplete = incomplete or ""
    return [bench for bench in _bench_names() if bench.startswith(incomplete)]


def _completable_sites(bench: str) -> list[str]:
    """The site names to offer for one bench: its own record, else what is on disk.

    This runs inside a shell completion, so it is allowed to know nothing and never allowed to
    raise: an unnamed bench, a missing bench and a bench whose config will not parse all offer
    nothing rather than putting a traceback where the operator expected a word. It also stays
    off the network and away from Docker for the same reason, which is why it reads the config
    file and the sites directory directly instead of going through `Bench`.
    """
    # Deferred: bench_config pulls in the site_manager package, which imports this module.
    from frappe_manager.site_manager.bench_config import BenchConfig

    bench_dir = CLI_BENCHES_DIRECTORY / bench
    config_path = bench_dir / CLI_BENCH_CONFIG_FILE_NAME

    if config_path.is_file():
        try:
            config = BenchConfig.import_from_toml(config_path)
            # `site_names` answers `[self.name]` for a bench that records no sites, which is a
            # bench name and not a site. Fall through to the disk instead of offering it.
            if config.sites:
                return config.site_names
        except Exception:
            pass

    return _sites_on_disk(bench_dir)


def _sites_on_disk(bench_dir: Path) -> list[str]:
    """Site directories under a bench's workspace, the fallback when the record cannot answer.

    A directory counts as a site when it holds a `site_config.json`; that is what separates the
    sites from `assets`, `apps.txt` and the rest of the bench's own furniture.
    """
    sites_dir = bench_dir / "workspace" / "frappe-bench" / "sites"
    try:
        entries = sorted(sites_dir.iterdir())
    except OSError:
        return []
    return [entry.name for entry in entries if (entry / "site_config.json").is_file()]


def _completable_domains(bench: str) -> list[str]:
    """Every hostname a bench serves: each site's own name, then that site's aliases.

    Wider than `_completable_sites` on purpose. A certificate is keyed by domain, so an alias is a
    valid `ssl` target and completing only site names would hide half the answers.
    """
    bench_dir = CLI_BENCHES_DIRECTORY / bench
    config_file = bench_dir / "bench_config.toml"
    if config_file.is_file():
        try:
            from frappe_manager.site_manager.bench_config import BenchConfig

            return BenchConfig.import_from_toml(config_file).domains
        except Exception:
            # A completion is never allowed to fail loudly; fall through to the directory walk.
            pass
    return _sites_on_disk(bench_dir)


def bench_domain_autocompletion_callback(incomplete: str = "") -> list[str]:
    """Shell completion for `BENCH[/DOMAIN]`.

    Before the separator this is bench-name completion. After it the bench is named, so the offer
    becomes that bench's served hostnames, each returned as the whole word because a completion
    replaces what was typed rather than appending to it.
    """
    incomplete = incomplete or ""
    bench, separator, domain_prefix = incomplete.partition(SEPARATOR)

    if not separator:
        return sites_autocompletion_callback(incomplete)

    return [f"{bench}{SEPARATOR}{d}" for d in _completable_domains(bench) if d.startswith(domain_prefix)]


def bench_site_autocompletion_callback(incomplete: str = "") -> list[str]:
    """Shell completion for the `BENCH[/SITE]` arguments.

    Before the separator this is bench-name completion, unchanged. Once the separator is typed
    the bench is already named, so the offer switches to that bench's sites, each returned as
    the whole `BENCH/SITE` word: a completion replaces what has been typed rather than
    appending to it, so a bare site name would complete `shop/b` to `b.example.com`.
    """
    incomplete = incomplete or ""
    bench, separator, site_prefix = incomplete.partition(SEPARATOR)

    if not separator:
        return sites_autocompletion_callback(incomplete)

    return [f"{bench}{SEPARATOR}{site}" for site in _completable_sites(bench) if site.startswith(site_prefix)]


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


def _pick_bench_name() -> str | None:
    """The bench menu, in the one place its prompt and options are written.

    None when there is no bench to offer. Exceptions PROPAGATE, because the two callers disagree
    about what an unanswerable prompt means and both are right: a parameter callback turns it into
    a `NonInteractiveError` naming the argument to pass, while a command body that has other modes
    to fall back on treats it as "no answer" and reports the address itself.
    """
    names = _bench_names()
    if not names:
        return None

    selected = get_global_output_handler().prompt_fuzzy(
        prompt="Select bench (↑↓ navigate, type to search)",
        choices=get_sorted_sites_list(names),
        vi_mode=True,
        mandatory=True,
        qmark="🤔",
        amark="🤔",
    )

    if selected:
        update_sites_cache(selected)

    return selected


def _resolve_bench(sitename: str | None) -> str:
    """A bench name: the CWD fallback, then the picker, then normalise and require it exists."""
    if not sitename:
        sitename = get_sitename_from_current_path()

    if not sitename:
        try:
            sitename = _pick_bench_name()
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


def bench_all_callback(sitename: str | None):
    """`BENCH` or `all` for the commands that can act on every bench in one run.

    `all` short-circuits the must-exist resolution below it: it is an address, not a name, so
    there is no directory to find. Everything else is `sitename_callback` exactly, including the
    refusal of a site part, because acting on every bench is still a bench-scoped act.
    """
    if sitename == RESERVED_BENCH_NAME:
        return sitename

    return sitename_callback(sitename)


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


def _resolve_bench_site(ctx: typer.Context, value: str | None, *, allow_all: bool) -> str | None:
    """`BENCH[/SITE]`, returning the BENCH and stashing the site half on `ctx.obj["site"]`.

    Returns the BENCH name, exactly as `sitename_callback` does, so command bodies keep
    receiving a plain bench-directory name and the 21 `Bench.get_object` call sites are
    untouched. A named site rides on `ctx.obj["site"]` instead.

    `allow_all` decides whether the reserved word `all` is a legal site half. It is off for the
    commands that address exactly one site, so `fm delete shop/all` and `fm reset shop/all` are
    refused by the PARSER rather than by a check each body has to remember: a missed check there
    would drop or reinstall every schema on the bench.
    """
    site = None

    if value:
        address = _parse_or_refuse(value)
        value = address.bench
        site = address.site

    bench = _resolve_bench(value)

    if site is not None:
        if allow_all and site == RESERVED_BENCH_NAME:
            # Carried through unresolved: the body fans out over `bench_config.site_names`, which is
            # the only place that knows what "all" means at the time it acts.
            if ctx.obj is not None:
                ctx.obj["site"] = RESERVED_BENCH_NAME
            return bench

        recorded = _recorded_sites(bench)
        # EXACT match first, and only then the `<name>.localhost` convenience form. Normalising
        # up front made a bare-label site unaddressable AND silently retargeted the command at a
        # different recorded site: on a bench serving both `shop` and `shop.localhost`,
        # `fm delete shop/shop` resolved to `shop.localhost` and offered to drop ITS database.
        # fm never creates a bare-label site, so this only arises from a hand-written config or
        # old data, which is exactly when silently acting on the wrong schema is least excusable.
        if site not in recorded:
            site = validate_sitename(site)
        if site not in recorded:
            known = ", ".join(f"'{s}'" for s in sorted(recorded))
            hint = f" Use '{bench}/all' for every site." if allow_all else ""
            raise typer.BadParameter(f"bench '{bench}' has no site '{site}'. It serves {known}.{hint}")
        # `ctx.obj` is None under --help, which short-circuits before app_callback fills it.
        if ctx.obj is not None:
            ctx.obj["site"] = site

    return bench


def bench_site_callback(ctx: typer.Context, value: str | None) -> str | None:
    """`BENCH[/SITE]` for the commands that act on exactly one site.

    `ctx` must be annotated: typer matches the context parameter by annotation and then
    takes the last un-annotated parameter as the value.
    """
    return _resolve_bench_site(ctx, value, allow_all=False)


def bench_site_all_callback(ctx: typer.Context, value: str | None) -> str | None:
    """`BENCH[/SITE|all]` for a command whose per-site work can fan out over the whole bench."""
    return _resolve_bench_site(ctx, value, allow_all=True)


def bench_domain_callback(ctx: typer.Context, value: str | None) -> str | None:
    """`BENCH[/DOMAIN]` for the `ssl` commands, where the second segment is a served HOSTNAME.

    A different second segment from `bench_site_callback`, and deliberately so. That one validates
    against `site_names`; a certificate is keyed by DOMAIN (`SSLCertificate.domain`), and a bench
    serves its sites' names AND their aliases. Every site name is a served domain, so the two agree
    wherever they overlap; this one additionally admits an alias, which is meaningful for a
    certificate and meaningless for a schema.

    Returns the bench UNCHANGED: no normalisation and no must-exist check, because these commands
    also manage domains belonging to no bench at all (`--standalone`), and in that mode this
    argument carries the external domain itself.

    The domain is NOT checked against the bench here. `bench_helpers` already does that with the
    bench loaded, and its refusal names the allowed domains and how to add one; doing it here would
    mean loading the config twice to say the same thing worse.
    """
    if not value:
        return value

    address = _parse_or_refuse(value)

    if address.site is not None and ctx.obj is not None:
        ctx.obj["domain"] = address.site

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
    """The bench menu for a command body rather than a parameter callback.

    The `ssl` subcommands cannot resolve their bench in the callback, because `--standalone` gives
    the same argument an external domain that belongs to no bench, so the refusal has to wait until
    the body knows which mode it is in. An unanswerable prompt is None here and the caller reports
    the address; :func:`_resolve_bench` raises instead.
    """
    if current_value:
        return current_value

    benchname = get_sitename_from_current_path()
    if benchname:
        return benchname

    try:
        return _pick_bench_name()
    except Exception:
        # No terminal to ask on. The caller's own error names the address it wanted.
        return None


def code_command_extensions_callback(extensions: list[str]) -> list[str]:
    extx = extensions + DEFAULT_EXTENSIONS
    unique_ext: set = set(extx)
    unique_ext_list: list[str] = [x for x in unique_ext]
    return unique_ext_list


def create_command_sitename_callback(ctx: typer.Context, sitename: str):
    """`BENCH` to create a bench, or `BENCH/SITE` to add a site to one that exists.

    The site half rides on `ctx.obj["site"]`, the same channel `bench_site_callback` uses, so the
    command body keeps receiving a bench name and nothing downstream has to learn a new type.
    """
    address = _parse_or_refuse(sitename)

    if address.bench == RESERVED_BENCH_NAME:
        # Checked before validate_sitename, which would turn it into `all.localhost`.
        raise typer.BadParameter(
            f"'{RESERVED_BENCH_NAME}' is reserved as an address meaning every bench, so it cannot be a bench name"
        )

    if address.site is not None:
        # Adding a site to an existing bench. The bench MUST exist: there is nothing to add to
        # otherwise, and creating both at once would hide which half the operator got wrong.
        _ = validate_sitename(address.bench)
        site = validate_sitename(address.site)
        bench = _resolve_bench(address.bench)

        recorded = _recorded_sites(bench)
        if site in recorded:
            raise typer.BadParameter(
                f"bench '{bench}' already serves the site '{site}'. It serves "
                f"{', '.join(repr(s) for s in sorted(recorded))}."
            )

        # `ctx.obj` is None under --help, which short-circuits before app_callback fills it.
        if ctx.obj is not None:
            ctx.obj["site"] = site
        return bench

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
