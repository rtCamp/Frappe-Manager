import os
import secrets
import string
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
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
from frappe_manager.commands.auth._helpers import read_password_from_stdin
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchConfig,
    BenchRuntime,
    DatabaseConfig,
    DatabaseEngine,
    Deployment,
    Deployments,
    FMBenchEnvType,
    RedisConfig,
    RestartPolicyEnum,
    SiteConfig,
    requests_immutable_runtime_inputs,
)
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.deploy_config_overlay import ConfigOverlayError, merge_overlays
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.site_manager.modules.compose_shape import unsupported_redis_scheme
from frappe_manager.utils.callbacks import (
    alias_domains_validation_callback,
    apps_list_validation_callback,
    create_command_sitename_callback,
)
from frappe_manager.utils.helpers import ImageRef
from frappe_manager.utils.process_lock import bench_lock
from frappe_manager.utils.site import validate_sitename

# Help-panel rules for `fm create --help`:
# 1. A title's FIRST word names the `BENCH/SITE` address segment the flags act on; scope is where
#    the value LANDS (`_FLAG_TO_CONFIG` / :func:`record_site`), never how the help text reads.
# 2. A parenthetical after a title only IDENTIFIES the category (`(mount runtime only)`); a
#    consequence belongs on the flag that causes it, stated once, never warned across panels.
# 3. Rich renders panels in signature order, so bench-scoped parameters are declared before the
#    first site-scoped one; moving a parameter reorders --help.
_PANEL_BENCH = "Bench Options"
_PANEL_RUNTIME = "Bench Options: Runtime"
_PANEL_MOUNT = "Bench Options: Workspace (mount runtime only)"
_PANEL_REDIS = "Bench Options: External Redis (every site)"
_PANEL_SITE = "Site Options"
_PANEL_DATABASE = "Site Options: External Database"


# The flags that are simply a config value under another name. Each maps to the TOML key path it
# writes; everything else about them (precedence, validation, defaults) is the merge and the model.
# Absent on purpose: `--app-image`/`--nginx-image` land in different keys per runtime (see
# _apply_app_image); `--bench-only`,
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
    "node_version": ("node_version",),
    "python_version": ("python_version",),
    "restart_policy": ("restart_policy",),
    "runtime": ("runtime",),
    "apps_from": ("apps_from",),
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
    line, so a field whose line was missing was silently dropped, which is how ``--apps-from`` came
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


def _apply_app_image(bc: BenchConfig, app_image: str, nginx_image: str | None) -> None:
    """Write ``--app-image`` to the key its runtime reads it from, with its companion.

    One flag, because the operator asks one thing: which image do the containers run. The
    runtimes PERSIST it differently, which is the only reason this is code and not another row in
    ``_FLAG_TO_CONFIG``: mount keeps the whole reference in top-level ``base_image`` and nothing
    ever rewrites it, while image runtime keeps the repository in ``image`` and the full reference
    in ``[deployments].current.app_image``, which ``fm switch`` moves on every deploy. Image
    validation belongs to ``BenchConfig.assert_runtime_coherent``.

    ``nginx_image`` is recorded beside it rather than worked out from it later. A mount bench has
    no companion to record: its nginx container runs fm's stock image.
    """
    if bc.runtime != BenchRuntime.image:
        bc.base_image = app_image
        return
    bc.image = ImageRef.parse(app_image).name or None
    bc.deployments = Deployments(
        current=Deployment(
            app_image=app_image,
            nginx_image=nginx_image,
            deployed_at=datetime.now(UTC).isoformat(),
            migrate_status="migrated",
        )
    )
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
    app_image: str | None,
    nginx_image: str | None,
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
    if app_image:
        _apply_app_image(bc, app_image, nginx_image)
    return bc


def _print_resolved_config(output, address: str, bc: BenchConfig) -> None:
    """Print the bench_config.toml this invocation WOULD write, and nothing else.

    Answers the one question `fm create`'s flag surface cannot: after create defaults, then each
    ``--config``, then the explicit flags, what is the bench actually going to be? The layering is
    resolved in `_build_bench_config` and was previously only observable by creating the bench and
    reading the file afterwards.

    Rendered through `export_to_toml`, the same writer `fm create` uses, rather than a second
    formatter: a preview that can disagree with what lands on disk is worse than no preview. That
    also means every field fm refuses to persist (`NOT_WRITTEN_TO_DISK`: the provisioning admin
    credentials, the generated DB password, `admin_pass`) is absent here for free, because it is
    absent from the real write.

    The output is valid `--config` input, so it round-trips: preview, save, pass back.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        rendered = Path(tmp) / "bench_config.toml"
        bc.export_to_toml(rendered)
        text = rendered.read_text()

    # `github_token` IS written to disk, so the writer keeps it -- but a preview goes to a terminal,
    # a scrollback buffer and any CI log that captures stdout, and it is the user's GitHub
    # credential rather than a bench-local generated one. Shown as present, never echoed.
    if bc.github_token:
        text = text.replace(bc.github_token, "<redacted>")

    # Plain lines, not a rich panel: this is a copy target (see commands/list.py:61 -- rich cells
    # truncate or fold, both of which corrupt a pasted path or an image ref).
    output.data_raw(f"# {address}: resolved bench_config.toml (nothing was created)")
    output.data_raw(text.rstrip())


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
    if not bc.apps_from:
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
    app_image: str | None,
    nginx_image: str | None,
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
        app_image=app_image,
        nginx_image=nginx_image,
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
    "db_type": "--db-type",
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

def _refuse_unhonoured_site_flags(ctx: typer.Context, *, bench_only: bool, added_site: str | None) -> None:
    """Refuse site-scoped flags on a path that would discard them, or that bind to the bench's
    primary site in a way `fm create BENCH/SITE` cannot honour.

    `--bench-only` skips `record_site` entirely, so `fm create shop --bench-only --db-host h
    --db-name n` used to accept a whole external database and create a bench on the mariadb
    container instead, throwing it away; and `--bench-only` beside a `BENCH/SITE` address is a
    straight contradiction that used to be resolved by ignoring the flag.

    `--attach-existing-site` is refused on `fm create BENCH/SITE` for a narrower reason:
    `_attach_existing_site` records the attached site as the bench's default and
    `_skip_phase6_for_attach` skips its app install, both of which only make sense for a bench's
    FIRST site. Every other database flag IS honoured on that path: `_add_site_to_bench` wires
    them through `_resolve_external_options` exactly like the bench-create path does.

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
        raise typer.Exit(2)

    if bench_only and given:
        output.display_error(
            f"--bench-only creates no site, so {', '.join(sorted(given))} would have nothing to "
            "apply to. Create the bench, then add the site with 'fm create BENCH/SITE' and pass them there."
        )
        raise typer.Exit(2)

    if added_site and "--attach-existing-site" in given:
        output.display_error(
            "'fm create BENCH/SITE' does not take --attach-existing-site: attach records the "
            "attached site as the bench's default and skips its own app install, both of which only "
            "make sense for a bench's first site. Create the bench and its attached site together "
            "with 'fm create BENCH --db-host ... --attach-existing-site' instead."
        )
        raise typer.Exit(2)


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
    secret = read_password_from_stdin()
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
    # is_file() rather than exists(): also catches a directory passed here, which click's own
    # dir_okay=True default would otherwise let through as "valid".
    if not absolute.is_file():
        raise typer.BadParameter(f"--db-ca: no such file: {db_ca}")
    # Reachable only because the Option below declares readable=False: click's own implicit
    # readable=True check would otherwise stat the RAW (unexpanded) argument and fail first, with
    # its own wording, for every path except a literal-tilde one (click's stat on an unexpanded
    # "~/..." string fails outright, and exists=False lets that through unchecked to here).
    if not os.access(absolute, os.R_OK):
        raise typer.BadParameter(f"--db-ca: file is not readable: {db_ca}")
    return str(absolute)


def _refuse_unsupported_redis_scheme(redis_cache: str, redis_queue: str) -> None:
    """Refuse a `[redis]` URL whose scheme fm cannot carry through to Frappe.

    Checked here, in the CLI, and deliberately NOT as a `RedisConfig` model validator:
    `RedisConfig(**dict(data["redis"]))` (`bench_config.py`'s `collect_from_data`) runs
    inside `import_from_toml`, which every command that loads this bench's
    `bench_config.toml` calls -- including `fm list`, `fm bake`, `fm switch` and `fm
    maintenance`, which skip the migration gate (`MIGRATION_CHECK_WHITELIST_COMMANDS` /
    `MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS`) specifically so one bench's bad file
    cannot take the rest of the host down with it. A pydantic validator that RAISES on
    read is exactly the incident `certificate.py`/`dns_provider.py`/`schema`
    moved away from (see their `extra="allow"` and `ConfigDict`/coercion comments): one
    hand-edited `[redis]` scheme would turn `fm list` into a host-wide outage. Checking
    once, at create time, before anything exists, refuses the same thing without
    reopening that hole for a hand edit fm never wrote in the first place.

    A hand edit is still not left silent, just not RAISING: `bench_config.py`'s
    `import_from_toml` calls the same `unsupported_redis_scheme` this does and warns
    (`warn_or_log`, never raise) on every load, the same tolerant treatment
    `certificate.py`/`dns_provider.py`/`schema` already use for a bad
    hand-edited value elsewhere in this file.

    The scheme test itself (`compose_shape.unsupported_redis_scheme`) is shared with
    that read-time warning, so an operator sees identical wording from either path.
    """
    for flag, url in (("--redis-cache", redis_cache), ("--redis-queue", redis_queue)):
        # Only the side that was named: an absent one means fm's own container, whose URL fm
        # builds itself and never needs checking.
        problem = unsupported_redis_scheme(url) if url else None
        if problem:
            raise typer.BadParameter(f"{flag}: {problem}")


def _resolve_redis(redis_cache: str | None, redis_queue: str | None) -> RedisConfig | None:
    """``[redis]`` from whichever sides were named, or None for the fm-managed redis containers.

    The two sides are independent: naming only ``--redis-queue`` moves the queue out and leaves
    the cache on fm's own container, which is the usual managed-redis shape (the stateful half is
    worth a provider, the throwaway latency-sensitive half is not). Frappe has always taken
    ``redis_cache`` and ``redis_queue`` as separate config keys; requiring both was fm's
    restriction, not the framework's.
    """
    if redis_cache is None and redis_queue is None:
        return None
    _refuse_unsupported_redis_scheme(redis_cache, redis_queue)
    try:
        return RedisConfig(cache=redis_cache, queue=redis_queue)
    except ValidationError as e:
        raise typer.BadParameter(f"--redis-cache / --redis-queue: {_first_error(e)}") from e


def mint_mariadb_schema_name(site: str) -> str:
    """The schema fm creates on its own `mariadb` container for `site`.

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
    round-trips as a bare `[sites."<name>"]` header, which is the record a bench on the mariadb
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
    database: DatabaseConfig | None = None,
    credentials: _ExternalCredentials | None = None,
) -> None:
    """Add `site` to the bench `benchname`, which already exists and may be serving.

    The order is the whole point, and it is NOT the order a fresh create uses. A create can bring
    routing up early because nothing is serving yet; here the bench's other sites are live, so the
    compose re-render and the nginx recreate go LAST, after the new site is known to work. Doing it
    first would take every existing site down for the duration of a `new-site` that may fail.

    Not run: the workspace and the apps are already cloned, the containers are already up, and the
    migration stamp already describes the bench. What runs is the site itself, its apps, and then
    the routing change.

    `database` and `credentials` are `_resolve_external_options`'s output, called by the caller
    exactly as the bench-create path calls it: `None` (the default) means fm's own server, for
    whichever engine `database.type` names.
    """
    output = get_global_output_handler()
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    # Asked for explicitly: adding a site to a bench whose workers are stopped must bring them up,
    # and the flags default off precisely so nothing else does this by accident.
    bench = bench_service.get_bench(benchname, start_workers_if_stopped=True, start_admin_tools_if_stopped=True)

    output.print(
        f"Adding site [fm.info]{site}[/fm.info] to bench [fm.info]{benchname}[/fm.info].",
        emoji_code=":globe_with_meridians:",
    )

    database = database or DatabaseConfig()

    # Recorded BEFORE `new-site`, because `get_site_config_data` and the TLS paths are keyed by site
    # and are read during creation. Saved to disk only once the site works, below.
    # `--alias-domains` names alternates for the site being ADDED, so they are recorded on its entry
    # here just as the fresh-create path records them on the first site's. Missing this is invisible
    # to a unit test of `record_site`: the flag simply never arrived, and the site was created with
    # an empty alias list while fm reported success.
    bench.bench_config.sites = record_site(bench.bench_config.sites, site, database, alias_domains)
    if credentials is not None:
        # Runtime-only fields: excluded from export_to_toml, so none of this reaches disk.
        bench.bench_config.db_admin_user = credentials.db_admin_user
        bench.bench_config.db_admin_password = credentials.db_admin_password
        bench.bench_config.db_password = credentials.db_password
        bench.bench_config.db_password_generated = credentials.db_password_generated
        bench.bench_config.attach_existing_site = credentials.attach_existing_site
        bench.bench_config.encryption_key = credentials.encryption_key

    schema = None
    if not database.external:
        # A schema of this site's own on fm's OWN server for this site's engine. Never the bench's
        # `db_name`: that one names the first site's schema, and two sites sharing a schema is data
        # loss.
        schema = mint_mariadb_schema_name(site)

    try:
        # Same funnel the bench-create pipeline's phase 3 uses before it ever reaches for `site`'s
        # database: brings this engine's container up (this bench may have only ever needed
        # mariadb before, and this site is `--db-type postgres`, or vice versa) and does not
        # return until it answers, so `new-site`/the probe below never race a container that just
        # started.
        bench.site_manager.wait_for_required_services(site=site)

        # No-op when `database` is fm's own server: the gate returns immediately, same as a
        # fresh-create whose first site has no `[database]` entry.
        bench.orchestrator.prepare_site_database(site)

        output.change_head(f"Creating site {site}")
        bench.site_manager.create_bench_site(
            site=site,
            db_name=database.name if database.external else schema,
            set_default=False,
        )

        if apps:
            output.change_head(f"Installing apps into {site}")
            bench.app_manager.install_apps_to_site(site)
    except Exception:
        # Site-scoped cleanup: the bench and its other sites are untouched. `remove_bench` is what a
        # failed CREATE calls and would be catastrophic here.
        output.stop()
        if database.external:
            schema_note = (
                f"schema {database.name} on {database.host} may hold partial data; it is not "
                "recorded in bench_config.toml, so nothing else refers to it."
            )
        else:
            schema_note = (
                f"a schema named {schema} may exist on fm's {database.type.value}; it is not "
                "recorded in bench_config.toml, so nothing else refers to it."
            )
        output.warning(
            f"Could not add {site}. The bench and its other sites are untouched. Any partial site "
            f"directory is at {bench.path / 'workspace' / 'frappe-bench' / 'sites' / site}, and "
            f"{schema_note}",
        )
        raise

    # The bench's upload limit and HSTS override both have to reach the new site before its
    # domain is routable. Nothing else does it: `apply_upload_limit`/`apply_hsts` run on create,
    # on `fm start` and (upload limit only) on `fm update --upload-limit`, so a site added between
    # those had no `vhost.d` entry of its own and inherited the global proxy's 1M default (and no
    # HSTS override, so the bench's own hardcoded header reached the browser unstripped) while the
    # bench advertised its real limit. A 2MB upload to the new site answered 413 from the proxy,
    # and `fm info` reported the bench limit either way. Both run unconditionally so a change to
    # just one is never skipped by short-circuiting the other.
    upload_limit_changed = bench.apply_upload_limit()
    hsts_changed = bench.apply_hsts()
    if (upload_limit_changed or hsts_changed) and bench.services.is_service_running("nginx-proxy"):
        bench.services.nginx_controller.reload()

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
    db_type: DatabaseEngine,
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
                "all; without it the bench uses fm's own container for this engine."
            )
        if configured is None:
            orphans = [flag for flag, given in credential_flags.items() if given]
            if orphans:
                raise typer.BadParameter(
                    f"{_flags(orphans)} an external database: pass --db-host, or declare [database] in a "
                    "--config overlay. Without one the bench uses fm's own container for this engine."
                )
            # No endpoint and no overlay: fm's own server for the engine asked for. The engine is
            # still recorded, because "which engine" and "whose server" are separate facts.
            return DatabaseConfig(type=db_type), redis, None
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
                type=db_type,
                host=db_host,
                # Unset unless typed, so an omitted port takes the ENGINE's default rather than
                # MariaDB's: the flag's own default cannot know which engine it is defaulting for.
                port=db_port if db_port_given else None,
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
    "Add a site to a bench that already exists",
    "{benchname}/b.example.com",
    detail="The bench and its other sites are untouched; only b.example.com is created.",
    benchname="mybench",
)
@example(
    "Add apps, pinned to a branch or not",
    "{benchname} --apps erpnext:version-16 --apps hrms",
    benchname="mybench",
)
@example(
    "Create a production bench",
    "{benchname} -e prod --apps erpnext",
    benchname="mybench",
)
@example(
    "Run a pre-built app image",
    "{benchname} --runtime image --app-image ghcr.io/acme/mybench:v15-20260822",
    detail="--app-image is the image the containers run; fm switch moves the bench to later images from there. Its companion is read from the image's own fm.nginx.image label, or named with --nginx-image.",
    benchname="mybench",
)
@example(
    "Take apps from a baked image instead of cloning them",
    "{benchname} --apps-from ghcr.io/acme/mybench:v15-20260822",
    detail="Copies that image's apps, env and built assets onto the host once, skipping clone and install. The bench still boots on the default base image unless --app-image says otherwise.",
    benchname="mybench",
)
@example(
    "Create a bench on an external database",
    "{benchname} --db-host db.example.com --db-name app_prod --db-password - --db-ca /etc/ssl/rds-bundle.pem",
    detail="Pass --db-admin-user with --db-admin-password instead of --db-password to have fm create the schema, the user and the grant.",
    benchname="mybench",
)
@example(
    "Clean up automatically in CI",
    "{benchname} --apps erpnext --remove-on-failure",
    detail="Pair with fm's own global -n: fm -n create {benchname} --apps erpnext --remove-on-failure removes the bench and its containers on failure instead of leaving them, and still exits non-zero either way.",
    benchname="mybench",
)
@bench_lock(param="address", operation="create")
def create(
    ctx: typer.Context,
    address: Annotated[
        str,
        typer.Argument(
            metavar="BENCH(/SITE)",
            help="Bench to create, or BENCH/SITE to add a site to a bench that already exists. The rule is the dot: 'shop' has none, so it serves 'shop.localhost' and works out of the box, while any name containing one is taken as a domain and served as typed, resolving only where you point it.",
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
    bench_only: Annotated[bool, typer.Option(help="Create the bench (config, directory, workspace or image, and containers) with no site in it. 'fm create BENCH/SITE' adds a site afterwards, into the workspace and containers already there. Every Site Option is ignored: there is no site yet for them to describe.")] = False,
    remove_on_failure: Annotated[
        bool,
        typer.Option(
            "--remove-on-failure",
            help="On failure, skip the removal prompt and remove the bench directory and its containers, interactively or not, instead of asking (interactive) or declining and reporting (non-interactive). The command still exits non-zero either way: this cleans up, it does not turn the failure into success. Never drops a schema on an external database (--db-host); that stays declined whether this is passed or not.",
            show_default=False,
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print the bench_config.toml this invocation would write, after --config and the flags are merged, and exit without creating anything.",
            show_default=False,
        ),
    ] = False,
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
    skip_version_check: Annotated[
        bool,
        typer.Option(
            "--skip-version-check",
            help="Accept a Python/Node version that does not satisfy frappe's requirement.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = False,
    restart_policy: Annotated[
        RestartPolicyEnum | None,
        typer.Option(
            "--restart-policy",
            help="Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).",
            show_default=False,
            rich_help_panel=_PANEL_BENCH,
        ),
    ] = None,
    runtime: Annotated[
        BenchRuntime | None,
        typer.Option(
            "--runtime",
            help="'mount' (default) live-mounts an editable workspace; 'image' runs a pre-built app image, moved to a new image with 'fm switch'.",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    app_image: Annotated[
        str | None,
        typer.Option(
            "--app-image",
            help="The image the bench's containers run, as an image reference (a repository plus a version, e.g. ghcr.io/acme/mybench:v15.2.1). Mount runtime: the base frappe image, with your editable workspace mounted over it. Image runtime: the pre-built app image itself, which is where the bench starts and which 'fm switch' later moves to another image.",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    nginx_image: Annotated[
        str | None,
        typer.Option(
            "--nginx-image",
            help="Image runtime: the companion assets image that serves this app image's static files. Recorded beside it, never worked out from its name. Omitted, fm reads the 'fm.nginx.image' label the bake stamped on the app image.",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    apps_from: Annotated[
        str | None,
        typer.Option(
            "--apps-from",
            help="Mount runtime: take the apps already built inside a baked image instead of cloning and installing them, named by an image reference. --apps, --python and --node then override what it carries. This is a one-time copy read at create, not an image the bench runs: see --app-image.",
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
    redis_cache: Annotated[
        str | None,
        typer.Option(
            "--redis-cache",
            help="External redis URL for the framework cache, e.g. redis://r.example:6379/0. Independent of the queue: either side may stay on fm's own container.",
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
    allow_domain_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-domain-conflicts",
            help="Skip the domain uniqueness check.",
            show_default=False,
            rich_help_panel=_PANEL_SITE,
        ),
    ] = False,
    alias_domains: Annotated[
        str | None,
        typer.Option(
            help="Extra domains THIS SITE answers on (comma-separated). Certificates come from 'fm ssl add'.",
            callback=alias_domains_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_SITE,
        ),
    ] = None,
    db_type: Annotated[
        DatabaseEngine,
        typer.Option(
            "--db-type",
            help="Database engine for this site: mariadb or postgres. Without --db-host it is fm's own server for that engine.",
            # NOT the external-database panel: this selects the ENGINE, and without --db-host it
            # selects which of fm's OWN servers the site lands on. Filing it under "External
            # Database" told the reader postgres was only reachable on someone else's server.
            rich_help_panel=_PANEL_SITE,
        ),
    ] = DatabaseEngine.mariadb,
    db_host: Annotated[
        str | None,
        typer.Option(
            "--db-host",
            help="External database host, replacing fm's own server for this site's engine. MySQL is not a supported backend.",
            show_default=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_port: Annotated[
        int,
        typer.Option(
            "--db-port",
            help="Port of the external database server. Defaults to the engine's own: 3306 or 5432.",
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
            # click's Path(readable=True) default stats the file itself and fails with its own
            # wording before this command body -- and _validated_ca()'s os.access check below --
            # ever runs. Disabled so a typo consistently gets fm's message, not click's.
            readable=False,
            rich_help_panel=_PANEL_DATABASE,
        ),
    ] = None,
    db_no_verify_hostname: Annotated[
        bool,
        typer.Option(
            "--db-no-verify-hostname",
            help="Check the certificate chain but not that the certificate names the host dialled. Applies to Frappe's own driver; fm's preflight uses the mariadb client, which verifies the hostname whenever a CA is set and cannot be told not to, so a certificate that cannot name the endpoint is still refused at create time.",
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
    Create a new bench, or add a site to one that already exists.

    fm create shop makes the bench shop serving the site shop.localhost. fm create shop/b.example.com adds b.example.com to the bench shop, leaving the sites already there untouched, and --bench-only makes an empty bench with no site at all. A bench name is just a name: it does not have to be a domain, and the site it serves is a separate thing from it.

    Which half of the address a flag acts on is written on its help panel: Bench Options apply either way, Site Options describe the site being created and are refused when there is no site to apply them to.

    Image runtime (--runtime image) refuses --apps, --python, --node and developer mode, which the image already carries.
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
        # This branch returns before the domain-uniqueness check below (over bench_config.domains)
        # ever runs, so it needs its own check here, or a site colliding with ANOTHER bench's
        # domain would succeed silently and --allow-domain-conflicts would be inert.
        output_early = get_global_output_handler()
        skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness
        try:
            validate_domains_unique(
                [added_site, *(alias_domains or [])],
                benches_root=CLI_BENCHES_DIRECTORY,
                exclude_bench=address,
                skip_check=skip_check,
            )
        except DomainConflictError as e:
            output_early.display_error(str(e))
            output_early.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
            raise typer.Exit(1) from e
        # `configured=None`: there is no `--config` overlay on this path (unlike the bench-create
        # path below), so there is never an already-merged `[database]` entry to defer to.
        # `redis_cache`/`redis_queue` are not threaded through here: `[redis]` is bench-wide, and
        # `--attach-existing-site` is refused above by `_refuse_unhonoured_site_flags`, so it is
        # always False by the time `_resolve_external_options` sees it.
        database_config, _, credentials = _resolve_external_options(
            configured=None,
            db_type=db_type,
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
            redis_cache=None,
            redis_queue=None,
        )
        _add_site_to_bench(
            # `benchname`, not `address`: this helper takes a bench DIRECTORY name, and the site it
            # adds arrives separately. A future rename of this command's `address` parameter must
            # not be swept into this kwarg name, or `fm create BENCH/SITE` raises TypeError.
            benchname=address,
            site=added_site,
            services_manager=services_manager,
            verbose=verbose,
            apps=cast("list[AppConfig]", apps),
            alias_domains=alias_domains,
            database=database_config,
            credentials=credentials,
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
    mariadb_name = mint_mariadb_schema_name(sitename)

    # One construction path: create defaults, then each --config overlay, then the flags the user
    # actually passed. Precedence is the merge order, so no field needs a per-field application step
    # that can fall out of step with the model -- a per-field check here previously let
    # `--runtime image` and an equivalent `--config` disagree on which flags were accepted.
    requested = {
        name for name in (*_FLAG_TO_CONFIG, "app_image", "nginx_image") if ctx.get_parameter_source(name) in _EXPLICIT_SOURCES
    }

    # Symmetric to `_refuse_immutable_inputs`: `--nginx-image` names the companion of an app
    # image, so without `--app-image` there is nothing to pair it with. It used to be accepted and
    # silently dropped -- `_apply_app_image` only runs under `if app_image` -- so a full build
    # finished on the stock nginx with the requested value recorded nowhere at all.
    if "nginx_image" in requested and "app_image" not in requested:
        raise typer.BadParameter(
            "--nginx-image names the companion assets image for an app image, so it needs "
            "--app-image. A mount bench builds its own assets and has no companion to name.",
        )
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
                    "node_version": node_version,
                    "python_version": python_version,
                    "restart_policy": restart_policy,
                    "runtime": runtime,
                    "apps_from": apps_from,
                },
            ),
            benchname=address,
            root_path=bench_config_path,
            app_image=app_image if "app_image" in requested else None,
            nginx_image=nginx_image if "nginx_image" in requested else None,
            db_name=mariadb_name,
        )
    except ConfigOverlayError as e:
        output.display_error(str(e))
        # The exception's own code, not a hardcoded 1: a bad `--config` value is a wrong command
        # line, and exit 2 is what every other bad value on it answers with.
        raise typer.Exit(e.exit_code) from e

    if bench_config.apps_from:
        output.print(
            f"Mount bench: seeding workspace from baked image [fm.info]{bench_config.apps_from}[/fm.info].",
            emoji_code=":package:",
        )
    if bench_config.runtime == BenchRuntime.image and bench_config.deployments and bench_config.deployments.current:
        output.print(
            f"Image bench: creating the site from pre-built image "
            f"[fm.info]{bench_config.deployments.current.app_image}[/fm.info].",
            emoji_code=":package:",
        )

    # External database / redis. Every refusal is raised here, before the bench directory,
    # the compose file or a single connection exists.
    database_config, redis_config, credentials = _resolve_external_options(
        configured=bench_config.get_database_config(sitename),
        db_type=db_type,
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
    # `fm shell` or which host to open. `--bench-only` never reaches `record_site` above, and stops
    # before the site is ever created (see `_run_creation` in bench_orchestrator.py), so this would
    # otherwise promise a site the invocation will not create, before phase 1 has even checked the
    # Docker images. `_report_bench_only_created` already tells the truth once that bench-only work
    # actually finishes ("Created bench: ..."), so this stays silent rather than pre-announce it.
    if sitename != address and not bench_only:
        output.print(
            f"Bench [fm.info]{address}[/fm.info] will serve the site [fm.info]{sitename}[/fm.info]",
            emoji_code=":globe_with_meridians:",
        )
    elif not bench_only:
        # The other branch of the same rule, which said NOTHING: a name containing a dot is taken
        # as already qualified, so no `.localhost` is appended and the site resolves only where the
        # operator points it. True of `a.example.com` and of `with.dots` alike -- fm cannot tell a
        # domain from a dotted word without a public-suffix list, and guessing would be worse than
        # saying what it did.
        output.print(
            f"Bench [fm.info]{address}[/fm.info] will serve the site [fm.info]{sitename}[/fm.info]: the name "
            "contains a dot, so fm took it as a domain and appended no .localhost. It resolves only where you "
            "point it, with a DNS record or a hosts entry.",
            emoji_code=":globe_with_meridians:",
        )

    # Keyed by the SITE, which is what `[sites]` holds: looking this up by the bench name finds
    # nothing the moment the two differ.
    site_database = bench_config.get_database_config(sitename)
    if site_database is not None:
        output.print(
            f"External database: this site lives on [fm.info]{site_database.host}:{site_database.resolved_port}"
            f"[/fm.info] in schema [fm.info]{site_database.name}[/fm.info], not the mariadb container",
            emoji_code=":floppy_disk:",
        )

    # `--newrelic`/`--newrelic-license-key` are gone (APM is `fm telemetry`), but `--config` still
    # carries a whole `[monitoring.newrelic]` table, so a TOML asking to monitor without a key
    # can still arrive here. Refused rather than created: the exporter emits no env vars without
    # the key, so the bench would record itself as monitored and report nothing.
    newrelic_config = bench_config.get_telemetry_config("newrelic")
    if newrelic_config and newrelic_config.enabled and not newrelic_config.license_key:
        raise typer.BadParameter(
            "[telemetry.newrelic] in --config sets enabled without a license_key. Add the key there, "
            "or create the bench and run 'fm telemetry enable BENCH newrelic --license-key KEY'."
        )

    all_domains = set(bench_config.domains)
    skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness
    try:
        # Always ASKED, even when the answer cannot refuse: skipping the check outright meant an
        # accepted conflict was completely silent, and two benches claiming one hostname is not a
        # neutral state -- the proxy round-robins between two different sites, so which one answers
        # a request is chance. Opting in is allowed; not being told is not.
        validate_domains_unique(all_domains, benches_root=CLI_BENCHES_DIRECTORY, skip_check=False)
    except DomainConflictError as e:
        if not skip_check:
            output.display_error(str(e))
            output.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
            raise typer.Exit(1) from e
        output.warning(str(e))
        output.warning(
            "Proceeding anyway. Both benches will answer for that hostname and the proxy will "
            "alternate between them, so which site a visitor reaches is not predictable."
        )

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

    if bench_config.restart_policy == RestartPolicyEnum.no and bench_config.environment_type == FMBenchEnvType.prod:
        output.warning("⚠️  Creating production bench with restart policy 'no'")
        output.warning("    Containers will not auto-recover from failures or system reboots")

    if dry_run:
        _print_resolved_config(output, address, bench_config)
        return

    with spinner(output, "Creating bench"):
        bench_service.create_bench(
            address,
            bench_config,
            bench_only=bench_only,
            remove_on_failure=remove_on_failure,
            skip_version_check=skip_version_check,
        )
