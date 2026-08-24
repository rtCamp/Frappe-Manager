import os
import secrets
import string
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

import tomlkit
import typer
from click.core import ParameterSource
from pydantic import ValidationError
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
    MonitoringConfig,
    NewRelicConfig,
    RedisConfig,
    RestartPolicyEnum,
)
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.deploy_config_overlay import ConfigOverlayError, merge_overlays
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.utils.callbacks import (
    alias_domains_validation_callback,
    apps_list_validation_callback,
    create_command_sitename_callback,
)
from frappe_manager.utils.helpers import has_explicit_tag
from frappe_manager.utils.site import validate_sitename

# Rich help panels for `fm create --help`, grouped by concern / runtime applicability
# (mirrors `fm update`).
_PANEL_RUNTIME = "Runtime Options"
_PANEL_MOUNT = "Mount Runtime Options (mount only)"
_PANEL_DOMAIN = "Domain Options"
_PANEL_MONITORING = "Monitoring Options"
_PANEL_EXTERNAL = "External Database and Redis Options"


def _resolve_deploy_options(
    runtime: BenchRuntime | None,
    base_image: str | None,
    apps: list,
    python_version: str | None,
    node_version: str | None,
) -> tuple[BenchRuntime, str | None, str | None, str | None]:
    """Resolve the deploy model (#323) for ``fm create``.

    Mode is selected only by ``--runtime`` (default ``mount``). ``--base-image`` is the
    image the bench's containers RUN, in both runtimes: on mount it sits under the
    editable workspace bind, on image runtime it IS the app. There is no ``--image``
    here, because on ``fm bake`` that word means the image being PRODUCED, and one word
    cannot point both ways.

    The two runtimes persist it differently, which is why the return tuple splits it.
    Mount keeps the whole ref in top-level ``base_image`` and nothing ever rewrites it.
    Image runtime keeps the repo in top-level ``image`` and the tag in
    ``[deploy_state].current_tag``, which ``fm switch`` moves on every deploy, so the
    value is a starting point rather than a pin.

    Returns ``(resolved_mode, image_repo, current_tag, base_image_override)``.
    """
    resolved = runtime or BenchRuntime.mount

    if resolved != BenchRuntime.image:
        base_image_override = None
        if base_image:
            if not has_explicit_tag(base_image):
                raise typer.BadParameter("--base-image must include a tag, e.g. 'ghcr.io/acme/frappe-custom:v15'.")
            base_image_override = base_image
        return resolved, None, None, base_image_override

    if not base_image:
        raise typer.BadParameter(
            "--runtime image requires --base-image <repo:tag>: the pre-built app image the bench runs, "
            "built by 'fm bake' or otherwise present/pullable.",
        )
    if not has_explicit_tag(base_image):
        raise typer.BadParameter(
            "--base-image must be a full reference with a tag, e.g. 'ghcr.io/acme/mybench:fm-20260722-abc123'.",
        )
    if apps:
        raise typer.BadParameter(
            "--apps is not supported in image mode; apps are baked into the image. "
            "Build the image with 'fm bake' (its --config/--apps).",
        )
    if python_version:
        raise typer.BadParameter("--python is not supported in image mode; the Python version is baked into the image.")
    if node_version:
        raise typer.BadParameter("--node is not supported in image mode; the Node version is baked into the image.")

    repo = base_image.rpartition(":")[0]
    return resolved, repo, base_image, None


def _validate_seed_image(
    seed_image: str,
    resolved_runtime: BenchRuntime,
) -> None:
    """``--seed-image`` contract: mount-only, explicit tag.

    Named for the ``seed_image`` key it writes. It is not an alternative to
    ``--base-image``: this copies a baked workspace onto the host ONCE at create and is
    then only a provenance record, while ``--base-image`` is read at every container
    start. The two compose.

    ``--apps`` entries are per-app overrides grafted on top of the seed;
    ``--python``/``--node`` swap the seeded toolchain (venv recreated, apps
    reinstalled) exactly like ``fm update``.
    """
    if resolved_runtime == BenchRuntime.image:
        raise typer.BadParameter(
            "--seed-image seeds a MOUNT workspace; an image runtime bench already runs the image it is given "
            "(use --base-image).",
        )
    if not has_explicit_tag(seed_image):
        raise typer.BadParameter("--seed-image requires an explicit ':tag' (e.g. local/myapp:20260724-abc).")


def _resolve_developer_mode(
    environment: FMBenchEnvType,
    resolved_runtime: BenchRuntime,
    explicit_enable: bool,
) -> bool:
    """Developer mode for a new bench.

    dev-environment benches default to enabled -- EXCEPT image runtime, where it
    is refused outright: DocType authoring writes app SOURCE files, and standard
    doctypes sync files -> DB (never DB -> files), so writes into an image
    bench's ephemeral container layer are unrecoverable schema-work loss.
    """
    if resolved_runtime == BenchRuntime.image:
        if explicit_enable:
            raise typer.BadParameter(
                "--developer-mode enable is not supported with image runtime: DocType authoring "
                "writes app files into the ephemeral container layer (lost on the next deploy, "
                "never re-derivable from the DB). Develop on a mount bench, or demote later with "
                "'fm update <bench> --runtime mount'.",
            )
        return False
    return environment == FMBenchEnvType.dev or explicit_enable


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


def _build_overlay_bench_config(
    *,
    config: list[str],
    benchname: str,
    root_path: Path,
    apps: list[AppConfig],
    environment: FMBenchEnvType,
    developer_mode_status: bool,
    admin_pass: str,
    alias_domains: list[str] | None,
    github_token: str | None,
    python_version: str | None,
    node_version: str | None,
    restart: RestartPolicyEnum | None,
    newrelic: bool,
    newrelic_license_key: str | None,
    runtime: BenchRuntime | None,
    base_image: str | None,
    db_name: str,
    explicit: set[str],
) -> tuple[BenchConfig, bool]:
    """Build a ``BenchConfig`` from a ``--config`` overlay with precedence B:
    explicit CLI flags > ``--config`` values > create defaults.

    ``explicit`` is the set of create parameter names the user actually passed
    (Click COMMANDLINE/ENVIRONMENT). Returns the config plus whether apps came
    from the user (flag or config) so the caller can gate repo validation.
    """
    seed = tomlkit.document()
    seed["name"] = benchname
    seed["developer_mode"] = False
    seed["admin_tools"] = False
    seed["environment"] = FMBenchEnvType.dev.value
    merged = merge_overlays(tomlkit.dumps(seed), config)

    handle = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)  # noqa: SIM115
    try:
        handle.write(merged)
        handle.close()
        bc = BenchConfig.import_from_toml(Path(handle.name))
    finally:
        Path(handle.name).unlink(missing_ok=True)

    apps_from_user = "apps" in explicit or bool(bc.apps_list)

    bc.name = benchname
    bc.root_path = root_path

    if "environment" in explicit:
        bc.environment_type = environment

    # Dev forces developer/admin tools on (create policy); prod honors flag/config.
    if bc.environment_type == FMBenchEnvType.dev:
        bc.developer_mode = True
        bc.admin_tools = True
    elif "developer_mode" in explicit:
        bc.developer_mode = developer_mode_status

    if "admin_pass" in explicit:
        bc.admin_pass = admin_pass
    if "alias_domains" in explicit:
        bc.alias_domains = list(alias_domains) if alias_domains else []
    if "github_token" in explicit:
        bc.github_token = github_token
    if "python_version" in explicit:
        bc.python_version = python_version
    if "node_version" in explicit:
        bc.node_version = node_version
    if "restart" in explicit:
        bc.restart_policy = restart
    if "newrelic" in explicit or "newrelic_license_key" in explicit:
        monitoring = bc.monitoring or MonitoringConfig()
        newrelic_config = monitoring.newrelic or NewRelicConfig()
        if "newrelic" in explicit:
            newrelic_config.enabled = newrelic
        if "newrelic_license_key" in explicit:
            newrelic_config.license_key = newrelic_license_key
        monitoring.newrelic = newrelic_config
        bc.monitoring = monitoring
    if not bc.db_name:
        bc.db_name = db_name

    if "apps" in explicit:
        bc.apps_list = _ensure_frappe_first(apps)
    else:
        bc.apps_list = _ensure_frappe_first(bc.apps_list)

    # Runtime/image selection: an explicit --runtime/--base-image re-resolves (flag path);
    # otherwise keep whatever the config declared ([runtime]/[deploy]/[deploy_state]).
    if "runtime" in explicit or "base_image" in explicit:
        r_runtime, r_image, r_tag, r_base = _resolve_deploy_options(
            runtime if "runtime" in explicit else bc.runtime,
            base_image if "base_image" in explicit else None,
            apps if "apps" in explicit else [],
            python_version if "python_version" in explicit else None,
            node_version if "node_version" in explicit else None,
        )
        bc.runtime = r_runtime
        bc.image = r_image
        bc.base_image = r_base
        bc.deploy_state = DeployState(current_tag=r_tag) if r_runtime == BenchRuntime.image else None

    return bc, apps_from_user


_DB_PASSWORD_ALPHABET = string.ascii_letters + string.digits

_EXPLICIT_SOURCES = (ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT, ParameterSource.PROMPT)


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
    benchname: Annotated[
        str,
        typer.Argument(
            help="Bench name, also its domain. A bare name becomes mybench.localhost.",
            callback=create_command_sitename_callback,
        ),
    ],
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
    environment: Annotated[
        FMBenchEnvType,
        typer.Option("--environment", "-e", help="Bench environment; sets the dev-mode and restart defaults."),
    ] = FMBenchEnvType.dev,
    developer_mode: Annotated[
        EnableDisableOptionsEnum,
        typer.Option(
            help="Let DocType edits write app source files. Already on for a dev-environment bench.",
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = EnableDisableOptionsEnum.disable,
    template: Annotated[bool, typer.Option(help="Create the bench config and directory only, no site.")] = False,
    admin_pass: Annotated[
        str,
        typer.Option(help="Administrator password."),
    ] = "admin",
    alias_domains: Annotated[
        str | None,
        typer.Option(
            help="Extra domains this bench answers on (comma-separated). Certificates come from 'fm ssl add'.",
            callback=alias_domains_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_DOMAIN,
        ),
    ] = None,
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
        ),
    ] = None,
    allow_domain_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-domain-conflicts",
            help="Skip the domain uniqueness check.",
            show_default=False,
            rich_help_panel=_PANEL_DOMAIN,
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
    db_host: Annotated[
        str | None,
        typer.Option(
            "--db-host",
            help="External MariaDB host, replacing fm's global-db container. MySQL is not a supported backend.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    db_port: Annotated[
        int,
        typer.Option(
            "--db-port",
            help="Port of the external database server.",
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = 3306,
    db_name: Annotated[
        str | None,
        typer.Option(
            "--db-name",
            help="Schema on that server this site lives in. Required with --db-host.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    db_user: Annotated[
        str | None,
        typer.Option(
            "--db-user",
            help="Login user for the schema. Defaults to the schema name, and must equal it on a v15 bench.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    db_password: Annotated[
        str | None,
        typer.Option(
            "--db-password",
            help="Password of the site's database login. Pass - for stdin; omit with --db-admin-user to generate one.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    db_admin_user: Annotated[
        str | None,
        typer.Option(
            "--db-admin-user",
            help="Administrative login, used once at create time to create the schema, the site user and the grant. Never stored.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    db_admin_password: Annotated[
        str | None,
        typer.Option(
            "--db-admin-password",
            help="Password for --db-admin-user. Pass - to read it from stdin.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    db_ca: Annotated[
        Path | None,
        typer.Option(
            "--db-ca",
            help="Host path to the CA bundle signing the server certificate. Required whenever the server enforces TLS.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    db_no_verify_hostname: Annotated[
        bool,
        typer.Option(
            "--db-no-verify-hostname",
            help="Check the certificate chain but not that the certificate names the host dialled.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = False,
    attach_existing_site: Annotated[
        bool,
        typer.Option(
            "--attach-existing-site",
            help="The schema already holds a Frappe site: build the bench around it and write nothing to the database.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = False,
    encryption_key: Annotated[
        str | None,
        typer.Option(
            "--encryption-key",
            help="The attached site's encryption_key, - to read from stdin. Without it Frappe mints a new one and existing encrypted secrets stop being readable.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    redis_cache: Annotated[
        str | None,
        typer.Option(
            "--redis-cache",
            help="External redis URL for the framework cache, e.g. redis://r.example:6379/0. Requires --redis-queue.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
    redis_queue: Annotated[
        str | None,
        typer.Option(
            "--redis-queue",
            help="External redis URL for the queue and realtime. Use a different logical index from --redis-cache: a restore mass-deletes the cache index.",
            show_default=False,
            rich_help_panel=_PANEL_EXTERNAL,
        ),
    ] = None,
):
    """
    Create a new bench and install apps into it.

    Image runtime (--runtime image) refuses --apps, --python, --node and developer mode, which the image already carries; 'fm update BENCHNAME --runtime mount' converts a bench to an editable workspace.
    """

    services_manager: ServicesManager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]
    fm_config: FMConfigManager = ctx.obj["fm_config_manager"]

    benchname = validate_sitename(benchname)
    output = get_global_output_handler()
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    bench_config_path = bench_service.benches_directory / benchname / CLI_BENCH_CONFIG_FILE_NAME

    developer_mode_status = developer_mode == EnableDisableOptionsEnum.enable
    apps_config = cast("list[AppConfig]", apps)
    sanitized_bench_name = benchname.replace(".", "_").replace("-", "_")
    # The schema fm mints on its own global-db container. Distinct from --db-name, which
    # names a schema on a server fm does not own.
    global_db_name = f"fm_{sanitized_bench_name}_{secrets.token_hex(8)}"

    if config:
        explicit = {
            name
            for name in (
                "environment",
                "developer_mode",
                "admin_pass",
                "alias_domains",
                "github_token",
                "python_version",
                "node_version",
                "restart",
                "newrelic",
                "newrelic_license_key",
                "runtime",
                "base_image",
                "apps",
            )
            if ctx.get_parameter_source(name)
            in (ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT, ParameterSource.PROMPT)
        }
        try:
            bench_config, apps_from_user = _build_overlay_bench_config(
                config=config,
                benchname=benchname,
                root_path=bench_config_path,
                apps=apps_config,
                environment=environment,
                developer_mode_status=developer_mode_status,
                admin_pass=admin_pass,
                alias_domains=alias_domains,
                github_token=github_token,
                python_version=python_version,
                node_version=node_version,
                restart=restart,
                newrelic=newrelic,
                newrelic_license_key=newrelic_license_key,
                runtime=runtime,
                base_image=base_image,
                db_name=global_db_name,
                explicit=explicit,
            )
        except ConfigOverlayError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e

        if bench_config.runtime == BenchRuntime.image:
            current_tag = bench_config.deploy_state.current_tag if bench_config.deploy_state else None
            if not current_tag:
                output.display_error(
                    "Image runtime needs a pre-built image: pass --base-image <repo:tag>, or set "
                    "top-level image + \\[deploy_state].current_tag in --config.",
                )
                raise typer.Exit(1)
            output.print(
                f"Image bench: creating the site from pre-built image [fm.info]{current_tag}[/fm.info].",
                emoji_code=":package:",
            )

        if bench_config.runtime == BenchRuntime.image and bench_config.developer_mode:
            output.display_error(
                "developer_mode = true is not supported with image runtime: DocType authoring writes "
                "app files into the ephemeral container layer (lost on the next deploy). Remove it "
                "from the config overlay or use runtime = 'mount'.",
            )
            raise typer.Exit(1)
    else:
        # For seeded creates --apps entries are overrides, used verbatim (no frappe
        # auto-injection -- the image's frappe must not be clobbered by a default).
        final_apps_list = apps_config if seed_image else _ensure_frappe_first(apps_config)

        # Deploy model (#323): resolve runtime (mount|image) plus --base-image, the image
        # the containers run in either runtime.
        resolved_runtime, image_repo, deploy_current_tag, base_image_override = _resolve_deploy_options(
            runtime, base_image, apps, python_version, node_version
        )
        if seed_image:
            _validate_seed_image(seed_image, resolved_runtime)
            output.print(
                f"Mount bench: seeding workspace from baked image [fm.info]{seed_image}[/fm.info].",
                emoji_code=":package:",
            )
        if resolved_runtime == BenchRuntime.image:
            output.print(
                f"Image bench: creating the site from pre-built image [fm.info]{deploy_current_tag}[/fm.info].",
                emoji_code=":package:",
            )

        bench_config = BenchConfig(
            name=benchname,
            apps_list=final_apps_list,
            developer_mode=_resolve_developer_mode(environment, resolved_runtime, developer_mode_status),
            admin_tools=True if environment == FMBenchEnvType.dev else False,
            admin_pass=admin_pass,
            environment_type=environment,
            root_path=bench_config_path,
            ssl_certificates=[],
            alias_domains=alias_domains if alias_domains else [],
            github_token=github_token,
            use_uv=True,
            python_version=python_version,
            node_version=node_version,
            db_name=global_db_name,
            restart_policy=restart,
            monitoring=MonitoringConfig(newrelic=NewRelicConfig(enabled=newrelic, license_key=newrelic_license_key))
            if newrelic or newrelic_license_key
            else None,
            runtime=resolved_runtime,
            image=image_repo,
            base_image=base_image_override,
            seed_image=seed_image,
            deploy_state=DeployState(current_tag=deploy_current_tag)
            if resolved_runtime == BenchRuntime.image
            else None,
        )
        apps_from_user = bool(apps)

    # --- shared validation + creation (both paths) ---
    # External database / redis. Every refusal is raised here, before the bench directory,
    # the compose file or a single connection exists.
    database_config, redis_config, credentials = _resolve_external_options(
        configured=bench_config.get_database_config(benchname),
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
    if database_config is not None:
        # Keyed by site name, which is the bench name today: when a bench can hold several
        # sites this becomes a data change rather than a schema change.
        bench_config.database = {**(bench_config.database or {}), benchname: database_config}
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

    site_database = bench_config.get_database_config(benchname)
    if site_database is not None:
        output.print(
            f"External database: this site lives on [fm.info]{site_database.host}:{site_database.port}"
            f"[/fm.info] in schema [fm.info]{site_database.name}[/fm.info], not the global-db container.",
            emoji_code=":floppy_disk:",
        )

    newrelic_config = bench_config.get_newrelic_config()
    if newrelic_config and newrelic_config.enabled and not newrelic_config.license_key:
        raise typer.BadParameter("--newrelic-license-key is required when --newrelic is set.")

    all_domains = {bench_config.name, *bench_config.alias_domains}
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
        bench_service.create_bench(benchname, bench_config, is_template=template)
