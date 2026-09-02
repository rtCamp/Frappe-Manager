import os
import secrets
import string
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, cast

import tomlkit
import typer
from click.core import ParameterSource
from pydantic import BaseModel, ValidationError
from typer_examples import example

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
)
from frappe_manager.commands.auth import _read_password_from_stdin
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchConfig,
    BenchRuntime,
    DatabaseConfig,
    DeployState,
    FMBenchEnvType,
    RedisConfig,
    RestartPolicyEnum,
    SiteConfig,
    requests_immutable_runtime_inputs,
)
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.deploy_config_overlay import ConfigOverlayError, merge_overlays
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.utils.callbacks import (
    alias_domains_validation_callback,
    apps_list_validation_callback,
    create_command_sitename_callback,
)
from frappe_manager.utils.site import validate_sitename

# Rich help panels for `fm create --help`. The FIRST word of every title is the segment of the
# `BENCH/SITE` address the flag acts on, because that is the question the address form raises and
# the help could not answer: `--redis-cache` serves every site the bench holds, `--db-name` names
# the schema of one. They used to share a box called "External Database and Redis Options".
#
# Scope is decided by where the value LANDS, not by how the help text reads: a top-level
# `BenchConfig` field is bench-scoped, an entry under `[sites."<site>"]` is site-scoped (see
# `_FLAG_TO_CONFIG` below and :func:`record_site`). Flags in the default "Options" box belong to
# neither: they decide whether the site half happens at all.
#
# Rich renders panels in order of first appearance in the signature, so every bench-scoped
# parameter is declared before the first site-scoped one. Moving one changes the help layout.
_PANEL_BENCH = "Bench Options"
_PANEL_RUNTIME = "Bench Options: Runtime"
_PANEL_MOUNT = "Bench Options: Workspace (mount runtime only)"
_PANEL_MONITORING = "Bench Options: Monitoring"
_PANEL_REDIS = "Bench Options: External Redis (every site)"
_PANEL_SITE = "Site Options (nothing to apply with --bench-only)"
_PANEL_DATABASE = "Site Options: External Database (nothing to apply with --bench-only)"


# The flags that are simply a config value under another name. Each maps to the TOML key path it
# writes; everything else about them (precedence, validation, defaults) is the merge and the model.
# Absent on purpose: `--base-image` is overloaded per runtime (see _apply_base_image); `--bench-only`,
# `--config` and `--allow-domain-conflicts` are not BenchConfig fields; the external database and
# redis flags are resolved separately because five of them are secrets that never reach disk;
# `--alias-domains` writes under `[sites."<site>"]`, a path this static map cannot express, so
# `record_site` applies it.
_FLAG_TO_CONFIG: dict[str, tuple[str, ...]] = {
    "admin_pass": ("admin_pass",),
    "apps": ("apps",),
    "developer_mode": ("developer_mode",),
    "environment": ("environment",),
    "github_token": ("github_token",),
    "newrelic": ("monitoring", "newrelic", "enabled"),
    "newrelic_license_key": ("monitoring", "newrelic", "license_key"),
    "node_version": ("node_version",),
    "python_version": ("python_version",),
    "restart": ("restart_policy",),
    "runtime": ("runtime",),
    "seed_image": ("seed_image",),
}


def _toml_value(value: object) -> object:
    """A flag's Python value as TOML-able data."""
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True, mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_toml_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _flag_overlay(requested: set[str], values: dict[str, object]) -> str:
    """The flags the user actually passed, rendered as the last overlay in the merge.

    This is what makes "an explicit flag beats --config" a property of the merge order rather than a
    per-field assignment. The previous shape applied each flag with its own ``if "name" in explicit``
    line, so a field whose line was missing was silently dropped, which is how ``--seed-image`` came
    to be ignored whenever ``--config`` was passed alongside it.
    """
    doc = tomlkit.document()
    for name in sorted(requested):
        path = _FLAG_TO_CONFIG.get(name)
        value = values.get(name)
        if path is None or value is None:
            continue
        target = doc
        for key in path[:-1]:
            if key not in target:
                target[key] = tomlkit.table()
            target = target[key]
        target[path[-1]] = _toml_value(value)
    return tomlkit.dumps(doc)


def _apply_base_image(bc: BenchConfig, base_image: str) -> None:
    """Write ``--base-image`` to the key its runtime reads it from.

    The flag is overloaded deliberately: it names the image the bench's containers RUN, in both
    runtimes. There is no ``--image`` here, because on ``fm bake`` that word means the image being
    PRODUCED, and one word cannot point both ways. The runtimes persist it differently, which is the
    only reason this is code and not another row in ``_FLAG_TO_CONFIG``: mount keeps the whole ref in
    top-level ``base_image`` and nothing ever rewrites it, while image runtime keeps the repo in
    ``image`` and the tag in ``[deploy_state].current_tag``, which ``fm switch`` moves on every
    deploy. Tag validation belongs to ``BenchConfig.assert_runtime_coherent``.
    """
    if bc.runtime != BenchRuntime.image:
        bc.base_image = base_image
        return
    bc.image = base_image.rpartition(":")[0] or None
    bc.deploy_state = DeployState(current_tag=base_image)
    bc.base_image = None


def _ensure_frappe_first(apps: list[AppConfig]) -> list[AppConfig]:
    """Frappe present and first (create's app-ordering rule)."""
    frappe_app = None
    others: list[AppConfig] = []
    for app in apps:
        if app.name == "frappe" or app.name.endswith("/frappe"):
            frappe_app = app
        else:
            others.append(app)
    if frappe_app is None:
        frappe_app = AppConfig.from_string(f"frappe:{STABLE_APP_BRANCH_MAPPING_LIST['frappe']}")
    return [frappe_app, *others]


def _build_bench_config(
    *,
    config: list[str],
    flag_overlay: str,
    benchname: str,
    root_path: Path,
    base_image: str | None,
) -> BenchConfig:
    """The one construction path: create defaults, then each ``--config``, then the flags.

    Precedence is the overlay order, later winning, so no field needs its own line to stay in step.
    """
    seed = tomlkit.document()
    seed["name"] = benchname
    seed["developer_mode"] = False
    seed["admin_tools"] = False
    seed["environment"] = FMBenchEnvType.dev.value
    merged = merge_overlays(tomlkit.dumps(seed), [*config, flag_overlay])

    handle = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)  # noqa: SIM115
    try:
        handle.write(merged)
        handle.close()
        bc = BenchConfig.import_from_toml(Path(handle.name))
    finally:
        Path(handle.name).unlink(missing_ok=True)

    bc.name = benchname
    bc.root_path = root_path
    if base_image:
        _apply_base_image(bc, base_image)
    return bc


def _refuse_immutable_inputs(bc: BenchConfig) -> None:
    """Refuse mount-only inputs on an image bench, whichever way they were spelled.

    Read off the merged config rather than off the flags, so ``--runtime image`` and a ``--config``
    declaring ``runtime = "image"`` reach the same answer. They did not before: the flag path refused
    ``--apps``/``--python``/``--node`` while the config path accepted them and left the values in
    bench_config.toml doing nothing.
    """
    if bc.runtime != BenchRuntime.image:
        return
    if not requests_immutable_runtime_inputs(
        python_version=bc.python_version,
        node_version=bc.node_version,
        apps=bc.apps_list,
        developer_mode_enable=bc.developer_mode,
    ):
        return
    raise typer.BadParameter(
        "image runtime carries its own apps, Python/Node toolchain and app sources, so apps, --python, --node and developer mode cannot be set for it, in flags or in --config. Bake them into the image with 'fm bake' (its --config/--apps), or create a mount bench.",
    )


def _derive_create_defaults(bc: BenchConfig, *, db_name: str) -> bool:
    """Create-time policy over the merged config, applied once whatever spelled it.

    Returns whether the app list came from the user, which is what gates repo validation: the
    default frappe entry injected below is not something to go and check against GitHub.
    """
    apps_from_user = bool(bc.apps_list)

    # An image bench can never carry developer mode (refused above when asked for). A dev bench gets
    # it, and the admin tools with it; prod honours whatever was passed.
    if bc.runtime == BenchRuntime.image:
        bc.developer_mode = False
    elif bc.environment_type == FMBenchEnvType.dev:
        bc.developer_mode = True
        bc.admin_tools = True

    # A seeded workspace already contains its own frappe, and injecting a default would clobber it.
    # There, --apps entries are per-app overrides used verbatim.
    if not bc.seed_image:
        bc.apps_list = _ensure_frappe_first(bc.apps_list)

    if not bc.db_name:
        bc.db_name = db_name

    return apps_from_user


def bench_config_from_inputs(
    *,
    config: list[str],
    flag_overlay: str,
    benchname: str,
    root_path: Path,
    base_image: str | None,
    db_name: str,
) -> tuple[BenchConfig, bool]:
    """Everything between the CLI parameters and ``create_bench``: merge, refuse, validate, derive.

    One function so there is one seam. ``create`` calls exactly this and nothing else on the way to
    a ``BenchConfig``, which is what lets a test exercise the real decision chain instead of
    re-assembling it and thereby entering the system downstream of any step that gets dropped.

    Returns the config plus whether the app list came from the user.
    """
    bc = _build_bench_config(
        config=config,
        flag_overlay=flag_overlay,
        benchname=benchname,
        root_path=root_path,
        base_image=base_image,
    )
    _refuse_immutable_inputs(bc)
    try:
        bc.assert_runtime_coherent()
    except ValueError as e:
        # The model states the rule; the CLI owns how a refusal reaches the operator.
        raise typer.BadParameter(str(e)) from e
    return bc, _derive_create_defaults(bc, db_name=db_name)


_DB_PASSWORD_ALPHABET = string.ascii_letters + string.digits

_EXPLICIT_SOURCES = (ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT, ParameterSource.PROMPT)

# The flags that describe a SITE rather than the bench: everything recorded under `[sites."<site>"]`
# by `record_site`. Kept as a flag-name map because a refusal has to name what the operator typed.
_SITE_SCOPED_FLAGS: dict[str, str] = {
    "alias_domains": "--alias-domains",
    "db_host": "--db-host",
    "db_port": "--db-port",
    "db_name": "--db-name",
    "db_user": "--db-user",
    "db_password": "--db-password",
    "db_admin_user": "--db-admin-user",
    "db_admin_password": "--db-admin-password",
    "db_ca": "--db-ca",
    "db_no_verify_hostname": "--db-no-verify-hostname",
    "attach_existing_site": "--attach-existing-site",
    "encryption_key": "--encryption-key",
}

# The subset `_add_site_to_bench` has no parameter for: adding a site to an existing bench records
# it with no database wiring of its own (`record_site(..., None, ...)`), so these cannot be honoured
# on that path. `--alias-domains` is absent because it IS forwarded there.
_SITE_DB_FLAGS = frozenset(_SITE_SCOPED_FLAGS) - {"alias_domains"}


def _refuse_unhonoured_site_flags(ctx: typer.Context, *, bench_only: bool, added_site: str | None) -> None:
    """Refuse site-scoped flags on a path that would discard them.

    All three of these used to exit 0 having thrown the flag away: `--bench-only` skips
    `record_site` entirely, so `fm create shop --bench-only --db-host h --db-name n` accepted a
    whole external database and created a bench on the global-db container instead; `fm create
    BENCH/SITE` reaches `_add_site_to_bench`, which takes no database arguments; and `--bench-only`
    beside a `BENCH/SITE` address is a straight contradiction that was resolved by ignoring the
    flag. Silently dropping database wiring is the worst of the three, because the bench comes up
    working and pointed at the wrong server.

    Only flags the operator actually passed count, so a default like `--db-port 3306` never trips.
    """
    given = {
        flag
        for name, flag in _SITE_SCOPED_FLAGS.items()
        if ctx.get_parameter_source(name) in _EXPLICIT_SOURCES
    }
    output = get_global_output_handler()

    if bench_only and added_site:
        output.display_error(
            "BENCH/SITE names a site to create and --bench-only says to create none. Pass the bench "
            "name alone for an empty bench, or drop --bench-only to create the site you named."
        )
        raise typer.Exit(1)

    if bench_only and given:
        output.display_error(
            f"--bench-only creates no site, so {', '.join(sorted(given))} would have nothing to "
            "apply to. Create the bench, then add the site with 'fm create BENCH/SITE' and pass them there."
        )
        raise typer.Exit(1)

    unhonoured = sorted(given & {_SITE_SCOPED_FLAGS[n] for n in _SITE_DB_FLAGS})
    if added_site and unhonoured:
        output.display_error(
            f"'fm create BENCH/SITE' does not take {', '.join(unhonoured)}: a site added to an "
            "existing bench is recorded without database wiring of its own. Create the bench and its "
            "first site together to point them at an external server."
        )
        raise typer.Exit(1)


@dataclass(frozen=True)
class _ExternalCredentials:
    """The create-time credentials, named exactly for the ``BenchConfig`` fields they fill.

    Those fields carry ``exclude=True`` and are in ``export_to_toml``'s exclude set, so
    they live for this run only: passwords stay out of ``bench_config.toml`` entirely and
    admin credentials stay out of every file, so no later fm run can provision or
    re-provision on someone's shared server.
    """

    db_admin_user: str | None
    db_admin_password: str | None
    db_password: str
    db_password_generated: bool
    attach_existing_site: bool
    encryption_key: str | None


def _generate_db_password(length: int = 24) -> str:
    """An alphanumeric password for the site's database login.

    Alphanumeric is a requirement rather than a preference: the same value lands in the
    ``[client]`` option file fm writes for the mariadb client, in ``mariadb`` command
    strings and in ``site_config.json``, and a quote or a '#' would break the CLI TLS
    path in a way that only surfaces during a backup. ``random_password_generate`` in
    utils/helpers.py builds on ``secrets.token_urlsafe``, which emits '-' and '_' even
    with symbols off, so this stays local rather than loosening that one for everyone.
    """
    return "".join(secrets.choice(_DB_PASSWORD_ALPHABET) for _ in range(length))


def _resolve_secret(value: str | None, flag: str) -> str | None:
    """'-' means read the secret from stdin, exactly as ``fm auth --password -`` does."""
    if value != "-":
        return value
    secret = _read_password_from_stdin()
    if not secret:
        raise typer.BadParameter(f"{flag} -: nothing was read from stdin.")
    return secret


def _first_error(error: ValidationError) -> str:
    """The message a model validator raised, without pydantic's framing."""
    return str(error.errors()[0]["msg"]).removeprefix("Value error, ")


def _flags(flags: list[str]) -> str:
    """'--db-name needs' or '--db-name, --db-user need', so a refusal names what tripped it."""
    return f"{', '.join(flags)} {'needs' if len(flags) == 1 else 'need'}"


def _validated_ca(db_ca: Path) -> str:
    """Absolute host path of a CA file that exists and can be read.

    Checked here so a typo fails on the command line rather than minutes later, when
    the bench directory exists and the copy into ``config/tls/<site>/`` is attempted.
    """
    # Absolute but deliberately not resolved: a certbot-style live/ symlink is the
    # rotation idiom, and `fm update --db-ca` records the path the same way.
    absolute = db_ca.expanduser().absolute()
    if not absolute.is_file():
        raise typer.BadParameter(f"--db-ca: no such file: {db_ca}")
    if not os.access(absolute, os.R_OK):
        raise typer.BadParameter(f"--db-ca: file is not readable: {db_ca}")
    return str(absolute)


def _resolve_redis(redis_cache: str | None, redis_queue: str | None) -> RedisConfig | None:
    """``[redis]`` from the flag pair, or None for the fm-managed redis containers."""
    if redis_cache is None and redis_queue is None:
        return None
    if not (redis_cache and redis_queue):
        missing = "--redis-queue" if redis_cache else "--redis-cache"
        raise typer.BadParameter(
            f"--redis-cache and --redis-queue must be given together, and {missing} is missing. A redis-less "
            "bench is not a thing: a missing redis_queue raises outright and redis_cache backs the document "
            "cache and sessions."
        )
    try:
        return RedisConfig(cache=redis_cache, queue=redis_queue)
    except ValidationError as e:
        raise typer.BadParameter(f"--redis-cache / --redis-queue: {_first_error(e)}") from e


def mint_global_db_schema_name(site: str) -> str:
    """The schema fm creates on its own `global-db` container for `site`.

    Off the SITE, not the bench. The schema belongs to the site, so two benches serving
    differently-named sites must not be able to collide here, and a bench renamed later must not
    imply a different schema. Distinct from `--db-name`, which names a schema on a server fm does
    not own. The random suffix is what actually guarantees uniqueness; the prefix is for a human
    reading `SHOW DATABASES`.
    """
    sanitized = site.replace(".", "_").replace("-", "_")
    return f"fm_{sanitized}_{secrets.token_hex(8)}"


def record_site(
    sites: dict[str, SiteConfig] | None,
    site: str,
    database: DatabaseConfig | None,
    alias_domains: list[str] | None = None,
) -> dict[str, SiteConfig]:
    """`[sites]` with `site` recorded, carrying `database` and any aliases when there are some.

    Every bench records its site, external database or not, keyed by the SITE name. This is the only
    place that survives the bench name and the site name being different: the directory says `shop`,
    this says `shop.localhost`, and `Bench.site_name` reads it back. An entry with no keys
    round-trips as a bare `[sites."<name>"]` header, which is the record a bench on the global-db
    container needs.

    `alias_domains` arrives here rather than through `_FLAG_TO_CONFIG` because its key path depends
    on the site name, which a static flag-to-path map cannot express.

    An entry already present is updated rather than replaced, so a `--config` overlay that described
    the site keeps whatever else it set. Aliases are only overwritten when the caller supplied some,
    so a later `record_site` for the same site does not silently drop them.
    """
    recorded = dict(sites or {})
    existing = recorded.get(site)
    update: dict[str, object] = {"database": database}
    if alias_domains is not None:
        update["alias_domains"] = list(alias_domains)
    recorded[site] = existing.model_copy(update=update) if existing else SiteConfig(**update)  # type: ignore[arg-type]
    return recorded

def _add_site_to_bench(
    *,
    benchname: str,
    site: str,
    services_manager: ServicesManager,
    verbose: bool,
    apps: list[AppConfig],
    alias_domains: list[str] | None = None,
) -> None:
    """Add `site` to the bench `benchname`, which already exists and may be serving.

    The order is the whole point, and it is NOT the order a fresh create uses. A create can bring
    routing up early because nothing is serving yet; here the bench's other sites are live, so the
    compose re-render and the nginx recreate go LAST, after the new site is known to work. Doing it
    first would take every existing site down for the duration of a `new-site` that may fail.

    Not run: the workspace and the apps are already cloned, the containers are already up, and the
    migration stamp already describes the bench. What runs is the site itself, its apps, and then
    the routing change.
    """
    output = get_global_output_handler()
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    bench = bench_service.get_bench(benchname)

    output.print(
        f"Adding site [fm.info]{site}[/fm.info] to bench [fm.info]{benchname}[/fm.info].",
        emoji_code=":globe_with_meridians:",
    )

    # A schema of this site's own on the global-db container. Never the bench's `db_name`: that one
    # names the first site's schema, and two sites sharing a schema is data loss.
    schema = mint_global_db_schema_name(site)

    # Recorded BEFORE `new-site`, because `get_site_config_data` and the TLS paths are keyed by site
    # and are read during creation. Saved to disk only once the site works, below.
    # `--alias-domains` names alternates for the site being ADDED, so they are recorded on its entry
    # here just as the fresh-create path records them on the first site's. Missing this is invisible
    # to a unit test of `record_site`: the flag simply never arrived, and the site was created with
    # an empty alias list while fm reported success.
    bench.bench_config.sites = record_site(bench.bench_config.sites, site, None, alias_domains)

    try:
        output.change_head(f"Creating site {site}")
        bench.site_manager.create_bench_site(site=site, db_name=schema, set_default=False)

        if apps:
            output.change_head(f"Installing apps into {site}")
            bench.app_manager.install_apps_to_site(site)
    except Exception:
        # Site-scoped cleanup: the bench and its other sites are untouched. `remove_bench` is what a
        # failed CREATE calls and would be catastrophic here.
        output.stop()
        output.warning(
            f"Could not add {site}. The bench and its other sites are untouched. Any partial site "
            f"directory is at {bench.path / 'workspace' / 'frappe-bench' / 'sites' / site}, and a "
            f"schema named {schema} may exist on global-db; neither is recorded in bench_config.toml, "
            f"so nothing else refers to them.",
        )
        raise

    # Routing last: the new site is in `[sites]`, so the republished map now carries its domain in
    # VIRTUAL_HOST and points it at this site in SITE_MAPPINGS. Until this runs the site exists and
    # works but is not reachable from outside, which is the safe half of the ordering.
    bench.save_bench_config(print_message=False)
    output.change_head("Publishing the new site's address")
    bench.republish_site_map()

    output.print(
        f"Added [fm.info]{site}[/fm.info]. The bench now serves "
        f"{', '.join(bench.bench_config.site_names)}.",
        emoji_code=":white_check_mark:",
    )




def _resolve_external_options(
    *,
    configured: DatabaseConfig | None,
    db_host: str | None,
    db_port: int,
    db_port_given: bool,
    db_name: str | None,
    db_user: str | None,
    db_password: str | None,
    db_admin_user: str | None,
    db_admin_password: str | None,
    db_ca: Path | None,
    db_no_verify_hostname: bool,
    attach_existing_site: bool,
    encryption_key: str | None,
    redis_cache: str | None,
    redis_queue: str | None,
) -> tuple[DatabaseConfig | None, RedisConfig | None, _ExternalCredentials | None]:
    """Validate the external database and redis flags and turn them into config.

    Two groups of flags, and they are not interchangeable. The **endpoint** is given on
    the command line as a whole, so every endpoint flag needs `--db-host` and the result
    replaces any entry a `--config` overlay held for this site. The **credentials** are
    never config, so they attach to whichever entry ends up configured, whether that came
    from the flags or from the overlay in `configured`.

    Every refusal happens here, before the bench directory, the compose file or a single
    connection exists. The one deliberate omission is "--db-password together with admin
    credentials against a schema that already exists": whether the schema exists is not
    knowable from the command line, so the probe owns that one.

    Returns the site's ``DatabaseConfig`` (None when the overlay's entry stands), the
    bench's ``RedisConfig`` and the credentials that must never reach disk.
    """
    redis = _resolve_redis(redis_cache, redis_queue)

    endpoint_flags = {
        "--db-port": db_port_given,
        "--db-name": db_name is not None,
        "--db-user": db_user is not None,
        "--db-ca": db_ca is not None,
        "--db-no-verify-hostname": db_no_verify_hostname,
    }
    credential_flags = {
        "--db-password": db_password is not None,
        "--db-admin-user": db_admin_user is not None,
        "--db-admin-password": db_admin_password is not None,
        "--attach-existing-site": attach_existing_site,
        "--encryption-key": encryption_key is not None,
    }

    if db_host is None:
        orphans = [flag for flag, given in endpoint_flags.items() if given]
        if orphans:
            raise typer.BadParameter(
                f"{_flags(orphans)} --db-host. The endpoint is given on the command line as a whole, or not at "
                "all; without it the bench uses the fm-managed global-db container."
            )
        if configured is None:
            orphans = [flag for flag, given in credential_flags.items() if given]
            if orphans:
                raise typer.BadParameter(
                    f"{_flags(orphans)} an external database: pass --db-host, or declare [database] in a "
                    "--config overlay. Without one the bench uses the fm-managed global-db container."
                )
            return None, redis, None
    elif not db_name:
        raise typer.BadParameter("--db-host requires --db-name: the schema on that server this site lives in.")

    admin_given = db_admin_user is not None or db_admin_password is not None
    if admin_given and not (db_admin_user and db_admin_password):
        raise typer.BadParameter(
            "--db-admin-user and --db-admin-password must be given together: fm provisions with both or with neither."
        )
    if attach_existing_site and admin_given:
        raise typer.BadParameter(
            "--attach-existing-site cannot be combined with --db-admin-user / --db-admin-password. Attach creates "
            "nothing and writes nothing to the schema, so an administrative login has no use there."
        )
    if db_password is None and not admin_given:
        raise typer.BadParameter(
            "an external database needs credentials, and neither path was taken: pass --db-password for a login "
            "that already exists on the server, or --db-admin-user with --db-admin-password so fm can have Frappe "
            "create the schema, the user and the grant. Supplying both is legal too, and means 'create the user "
            "with this password'. Passwords are deliberately not config, so a [database] overlay cannot carry "
            "them either."
        )

    if db_no_verify_hostname and db_ca is None:
        raise typer.BadParameter(
            "--db-no-verify-hostname needs --db-ca. Without a CA the driver sends no TLS at all, so there is no "
            "certificate whose hostname could be checked."
        )

    ca_path = _validated_ca(db_ca) if db_ca is not None else None

    # Refusals are done; only now is it safe to consume stdin.
    db_password = _resolve_secret(db_password, "--db-password")
    db_admin_password = _resolve_secret(db_admin_password, "--db-admin-password")
    encryption_key = _resolve_secret(encryption_key, "--encryption-key")

    database = None
    if db_host is not None:
        try:
            database = DatabaseConfig(
                host=db_host,
                port=db_port,
                name=db_name,
                user=db_user,
                ca=ca_path,
                check_hostname=not db_no_verify_hostname,
            )
        except ValidationError as e:
            raise typer.BadParameter(f"--db-host / --db-port / --db-name / --db-user: {_first_error(e)}") from e

    # Something must exist before setup_database runs: it creates the user from
    # frappe.conf.db_password, read out of the site file fm writes first. Whether fm
    # minted it is load-bearing for the probe: create_user is CREATE USER IF NOT EXISTS,
    # so an account that already exists keeps a password fm does not know.
    credentials = _ExternalCredentials(
        db_admin_user=db_admin_user,
        db_admin_password=db_admin_password,
        db_password=db_password if db_password is not None else _generate_db_password(),
        db_password_generated=db_password is None,
        attach_existing_site=attach_existing_site,
        encryption_key=encryption_key,
    )
    return database, redis, credentials


@example(
    "Create a bench with Frappe only",
    "{benchname}",
    benchname="mybench",
)
@example(
    "Add apps, pinned to a branch or not",
    "{benchname} --apps erpnext:version-15 --apps hrms",
    benchname="mybench",
)
@example(
    "Create a production bench",
    "{benchname} -e prod --apps erpnext",
    benchname="mybench",
)
@example(
    "Run a pre-built app image",
    "{benchname} --runtime image --base-image ghcr.io/acme/mybench:v15-20260822",
    detail="--base-image is the image the containers run. Here it is the app image itself, and fm switch moves the bench to later tags from there.",
    benchname="mybench",
)
@example(
    "Seed an editable workspace from a baked image",
    "{benchname} --seed-image ghcr.io/acme/mybench:v15-20260822",
    detail="Copies the image's apps, env and built assets onto the host once, skipping clone and install. The bench still boots on the default base image unless --base-image says otherwise.",
    benchname="mybench",
)
@example(
    "Create a bench on an external database",
    "{benchname} --db-host db.example.com --db-name app_prod --db-password - --db-ca /etc/ssl/rds-bundle.pem",
    detail="Pass --db-admin-user with --db-admin-password instead of --db-password to have fm create the schema, the user and the grant.",
    benchname="mybench",
)
def create(
    ctx: typer.Context,
    address: Annotated[
        str,
        typer.Argument(
            metavar="BENCH(/SITE)",
            help="Bench to create, or BENCH/SITE to add a site to a bench that already exists. A bench name is just a name: 'shop' creates a bench 'shop' serving a site 'shop.localhost', and a name that is already a domain serves that domain.",
            callback=create_command_sitename_callback,
        ),
    ],
    environment: Annotated[
        FMBenchEnvType,
        typer.Option(
            "--environment",
            "-e",
            help="Bench environment; sets the dev-mode and restart defaults.",
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = FMBenchEnvType.dev,
    apps: Annotated[
        list[str],
        typer.Option(
            "--apps",
            "-a",
            help="App to install: appname or owner/repo, optional :branch (repeatable). Frappe is always first.",
            callback=apps_list_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = [],
    developer_mode: Annotated[
        EnableDisableOptionsEnum,
        typer.Option(
            help="Let DocType edits write app source files. Already on for a dev-environment bench.",
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = EnableDisableOptionsEnum.disable,
    bench_only: Annotated[bool, typer.Option(help="Create the bench (config, directory, containers) with no site in it. Sites are added afterwards with 'fm create BENCH/SITE'.")] = False,
    github_token: Annotated[
        str | None,
        typer.Option(
            "--github-token",
            "-t",
            help="Token for cloning private app repos.",
            envvar="GITHUB_TOKEN",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    python_version: Annotated[
        str | None,
        typer.Option(
            "--python",
            help="Python version, e.g. '3.11'. Auto-detected by default.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    node_version: Annotated[
        str | None,
        typer.Option(
            "--node",
            help="Node version, e.g. '20'. Auto-detected by default.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    restart: Annotated[
        RestartPolicyEnum | None,
        typer.Option(
            "--restart",
            help="Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    allow_domain_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-domain-conflicts",
            help="Skip the domain uniqueness check.",
            show_default=False,
        ),
    ] = False,
    runtime: Annotated[
        BenchRuntime | None,
        typer.Option(
            "--runtime",
            help="'mount' (default) live-mounts an editable workspace; 'image' runs a pre-built app image, moved to a new tag with 'fm switch'.",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    base_image: Annotated[
        str | None,
        typer.Option(
            "--base-image",
            help="The image the bench's containers run (repo:tag). Mount runtime: the base frappe image, with your editable workspace mounted over it. Image runtime: the pre-built app image itself, which is where the bench starts and which 'fm switch' later moves to another tag.",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    seed_image: Annotated[
        str | None,
        typer.Option(
            "--seed-image",
            help="Mount runtime: seed the workspace from a baked app image (repo:tag) instead of cloning and installing apps. --apps, --python and --node then override what it carries. This is a one-time copy, not what the containers run: see --base-image.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    config: Annotated[
        list[str],
        typer.Option(
            "--config",
            help="TOML base config: file path or inline. Explicit flags win; later --config wins.",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = [],
    newrelic: Annotated[
        bool,
        typer.Option(
            "--newrelic/--no-newrelic",
            help="Enable NewRelic APM for the web process.",
            show_default=False,
            rich_help_panel=_PANEL_MONITORING,
        ),
    ] = False,
    newrelic_license_key: Annotated[
        str | None,
        typer.Option(
            "--newrelic-license-key",
            help="NewRelic ingest license key. Required with --newrelic.",
            show_default=False,
            rich_help_panel=_PANEL_MONITORING,
        ),
    ] = None,
    redis_cache: Annotated[
        str | None,
        typer.Option(
            "--redis-cache",
            help="External redis URL for the framework cache, e.g. redis://r.example:6379/0. Requires --redis-queue.",
            show_default=False,
            rich_help_panel=_PANEL_REDIS,
        ),
    ] = None,
    redis_queue: Annotated[
        str | None,
        typer.Option(
            "--redis-queue",
            help="External redis URL for the queue and realtime. Use a different logical index from --redis-cache: a restore mass-deletes the cache index.",
            show_default=False,
            rich_help_panel=_PANEL_REDIS,
        ),
    ] = None,
    admin_pass: Annotated[
        str,
        typer.Option(
            help="Administrator password for sites created on this bench.",
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = "admin",
    alias_domains: Annotated[
        str | None,
        typer.Option(
            help="Extra domains THIS SITE answers on (comma-separated). Certificates come from 'fm ssl add'.",
            callback=alias_domains_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_SITE,
        ),
    ] = None,
    db_host: Annotated[
        str | None,
        typer.Option(
            "--db-host",
            help="External MariaDB host, replacing fm's global-db container. MySQL is not a supported backend.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_port: Annotated[
        int,
        typer.Option(
            "--db-port",
            help="Port of the external database server.",
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = 3306,
    db_name: Annotated[
        str | None,
        typer.Option(
            "--db-name",
            help="Schema on that server this site lives in. Required with --db-host.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_user: Annotated[
        str | None,
        typer.Option(
            "--db-user",
            help="Login user for the schema. Defaults to the schema name, and must equal it on a v15 bench.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_password: Annotated[
        str | None,
        typer.Option(
            "--db-password",
            help="Password of the site's database login. Pass - for stdin; omit with --db-admin-user to generate one.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_admin_user: Annotated[
        str | None,
        typer.Option(
            "--db-admin-user",
            help="Administrative login, used once at create time to create the schema, the site user and the grant. Never stored.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_admin_password: Annotated[
        str | None,
        typer.Option(
            "--db-admin-password",
            help="Password for --db-admin-user. Pass - to read it from stdin.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_ca: Annotated[
        Path | None,
        typer.Option(
            "--db-ca",
            help="Host path to the CA bundle signing the server certificate. Required whenever the server enforces TLS.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_no_verify_hostname: Annotated[
        bool,
        typer.Option(
            "--db-no-verify-hostname",
            help="Check the certificate chain but not that the certificate names the host dialled.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = False,
    attach_existing_site: Annotated[
        bool,
        typer.Option(
            "--attach-existing-site",
            help="The schema already holds a Frappe site: build the bench around it and write nothing to the database.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = False,
    encryption_key: Annotated[
        str | None,
        typer.Option(
            "--encryption-key",
            help="The attached site's encryption_key, - to read from stdin. Without it Frappe mints a new one and existing encrypted secrets stop being readable.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
):
    """
    Create a new bench and install apps into it.

    Image runtime (--runtime image) refuses --apps, --python, --node and developer mode, which the image already carries; 'fm update BENCH --runtime mount' converts a bench to an editable workspace.
    """

    services_manager: ServicesManager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]
    fm_config: FMConfigManager = ctx.obj["fm_config_manager"]

    added_site = ctx.obj.get("site") if ctx.obj else None
    _refuse_unhonoured_site_flags(ctx, bench_only=bench_only, added_site=added_site)

    # `BENCH/SITE` adds a site to a bench that already exists. The callback resolved the bench and
    # put the new site here, so this branch has to come before ANY bench-creation work: phase 1
    # mkdirs and re-renders compose, which on a running bench would disturb the sites already
    # serving before the new one is known to work.
    if added_site:
        _add_site_to_bench(
            address=address,
            site=added_site,
            services_manager=services_manager,
            verbose=verbose,
            apps=cast("list[AppConfig]", apps),
            alias_domains=alias_domains,
        )
        return

    # The BENCH keeps the name as typed; the SITE is its FQDN form. `fm create shop` yields bench
    # `shop` serving site `shop.localhost`, and `fm create a.example.com` yields bench
    # `a.example.com` serving `a.example.com`, because a name that is already a domain is left
    # alone. This is the one place the two are minted, and everything downstream reads them apart.
    sitename = validate_sitename(address)
    output = get_global_output_handler()
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    bench_config_path = bench_service.benches_directory / address / CLI_BENCH_CONFIG_FILE_NAME

    developer_mode_status = developer_mode == EnableDisableOptionsEnum.enable
    apps_config = cast("list[AppConfig]", apps)
    global_db_name = mint_global_db_schema_name(sitename)

    # One construction path: create defaults, then each --config overlay, then the flags the user
    # actually passed. Precedence is the merge order, so no field needs a per-field application step
    # that can fall out of step with the model. Two paths used to disagree here: `--runtime image`
    # refused --apps/--python/--node while a --config declaring `runtime = "image"` accepted them.
    requested = {
        name for name in (*_FLAG_TO_CONFIG, "base_image") if ctx.get_parameter_source(name) in _EXPLICIT_SOURCES
    }
    try:
        bench_config, apps_from_user = bench_config_from_inputs(
            config=config,
            flag_overlay=_flag_overlay(
                requested,
                {
                    "admin_pass": admin_pass,
                    "apps": apps_config,
                    "developer_mode": developer_mode_status,
                    "environment": environment,
                    "github_token": github_token,
                    "newrelic": newrelic,
                    "newrelic_license_key": newrelic_license_key,
                    "node_version": node_version,
                    "python_version": python_version,
                    "restart": restart,
                    "runtime": runtime,
                    "seed_image": seed_image,
                },
            ),
            address=address,
            root_path=bench_config_path,
            base_image=base_image if "base_image" in requested else None,
            db_name=global_db_name,
        )
    except ConfigOverlayError as e:
        output.display_error(str(e))
        raise typer.Exit(1) from e

    if bench_config.seed_image:
        output.print(
            f"Mount bench: seeding workspace from baked image [fm.info]{bench_config.seed_image}[/fm.info].",
            emoji_code=":package:",
        )
    if bench_config.runtime == BenchRuntime.image and bench_config.deploy_state:
        output.print(
            f"Image bench: creating the site from pre-built image "
            f"[fm.info]{bench_config.deploy_state.current_tag}[/fm.info].",
            emoji_code=":package:",
        )

    # External database / redis. Every refusal is raised here, before the bench directory,
    # the compose file or a single connection exists.
    database_config, redis_config, credentials = _resolve_external_options(
        configured=bench_config.get_database_config(sitename),
        db_host=db_host,
        db_port=db_port,
        db_port_given=ctx.get_parameter_source("db_port") in _EXPLICIT_SOURCES,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_admin_user=db_admin_user,
        db_admin_password=db_admin_password,
        db_ca=db_ca,
        db_no_verify_hostname=db_no_verify_hostname,
        attach_existing_site=attach_existing_site,
        encryption_key=encryption_key,
        redis_cache=redis_cache,
        redis_queue=redis_queue,
    )
    # `--bench-only` stops before the site is created, so recording one would have `[sites]` claim a
    # site that has no directory, no schema and no `site_config.json`. Everything downstream trusts
    # that table: `fm list` and `fm info` reported the phantom, `Bench.site_name` resolved to it,
    # routing published a VIRTUAL_HOST entry for it, and `fm delete --all-sites` would have gone
    # looking for its schema. An empty table is the correct record of a bench with no sites, and it
    # is exactly what deleting the last site leaves behind.
    if not bench_only:
        bench_config.sites = record_site(bench_config.sites, sitename, database_config, alias_domains)
    if redis_config is not None:
        bench_config.redis = redis_config
    if credentials is not None:
        # Runtime-only fields: excluded from export_to_toml, so none of this reaches disk.
        bench_config.db_admin_user = credentials.db_admin_user
        bench_config.db_admin_password = credentials.db_admin_password
        bench_config.db_password = credentials.db_password
        bench_config.db_password_generated = credentials.db_password_generated
        bench_config.attach_existing_site = credentials.attach_existing_site
        bench_config.encryption_key = credentials.encryption_key

    # Say both names out loud. `fm create shop` makes a bench called `shop` serving a site called
    # `shop.localhost`, and an operator who is told only one of them cannot tell which to type at
    # `fm shell` or which host to open.
    if sitename != address:
        output.print(
            f"Bench [fm.info]{address}[/fm.info] will serve the site [fm.info]{sitename}[/fm.info].",
            emoji_code=":globe_with_meridians:",
        )

    # Keyed by the SITE, which is what `[sites]` holds: looking this up by the bench name finds
    # nothing the moment the two differ.
    site_database = bench_config.get_database_config(sitename)
    if site_database is not None:
        output.print(
            f"External database: this site lives on [fm.info]{site_database.host}:{site_database.port}"
            f"[/fm.info] in schema [fm.info]{site_database.name}[/fm.info], not the global-db container.",
            emoji_code=":floppy_disk:",
        )

    newrelic_config = bench_config.get_newrelic_config()
    if newrelic_config and newrelic_config.enabled and not newrelic_config.license_key:
        raise typer.BadParameter("--newrelic-license-key is required when --newrelic is set.")

    all_domains = set(bench_config.domains)
    skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness
    try:
        validate_domains_unique(all_domains, benches_root=CLI_BENCHES_DIRECTORY, skip_check=skip_check)
    except DomainConflictError as e:
        output.display_error(str(e))
        output.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
        raise typer.Exit(1) from e

    if apps_from_user:
        apps_to_check = bench_config.get_apps_config()

        with spinner(output, f"Validating {len(apps_to_check)} app repositories"):
            validation_result = AppConfig.validate_repos_batch(apps_to_check, bench_config.github_token)

        for result in validation_result.results:
            if result.success:
                output.print(result.display_message, emoji_code=":white_check_mark:")
            else:
                output.display_error(result.display_message, emoji_code=":cross_mark:")

        if not validation_result.all_valid:
            output.display_error(
                f"\n⚠️  {validation_result.failure_count}/{len(apps_to_check)} repositories failed validation",
            )
            output.display_error("Please check the repository names, branches, and authentication")
            raise typer.Exit(1)

    # Warn if prod bench is being created with restart: no
    if bench_config.restart_policy == RestartPolicyEnum.no and bench_config.environment_type == FMBenchEnvType.prod:
        output.warning("⚠️  Creating production bench with restart policy 'no'")
        output.warning("    Containers will not auto-recover from failures or system reboots")

    with spinner(output, "Creating bench"):
        bench_service.create_bench(address, bench_config, bench_only=bench_only)
