import secrets
import tempfile
from pathlib import Path
from typing import Annotated, cast

import tomlkit
import typer
from click.core import ParameterSource
from typer_examples import example

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
)
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler, spinner
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import (
    AppConfig,
    BenchConfig,
    BenchRuntime,
    DeployState,
    FMBenchEnvType,
    RestartPolicyEnum,
)
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.deploy_config_overlay import ConfigOverlayError, merge_overlays
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.utils.callbacks import (
    alias_domains_validation_callback,
    apps_list_validation_callback,
)
from frappe_manager.utils.site import validate_sitename

# Rich help panels for `fm create --help`, grouped by concern / runtime applicability
# (mirrors `fm update`).
_PANEL_RUNTIME = "Runtime Options"
_PANEL_MOUNT = "Mount Runtime Options (mount only)"
_PANEL_DOMAIN = "Domain Options"
_PANEL_MONITORING = "Monitoring Options"


def _has_explicit_tag(image_ref: str) -> bool:
    """True when image_ref carries a :tag (':' after the last '/'), so a bare
    'localhost:5000/repo' host-port is not mistaken for a tag."""
    return ":" in image_ref.rsplit("/", 1)[-1]


def _resolve_deploy_options(
    runtime: BenchRuntime | None,
    image: str | None,
    apps: list,
    python_version: str | None,
    node_version: str | None,
) -> tuple[BenchRuntime, str | None, str | None, str | None]:
    """Resolve the deploy model (#323) for ``fm create``.

    Mode is selected only by ``--runtime`` (default ``mount``); ``--image``
    no longer implies image mode. ``--image`` is mode-scoped: in mount mode it
    overrides the base frappe image, in image mode it is the pre-built app image
    to run. Returns ``(resolved_mode, image_repo, current_tag, base_image_override)``.
    """
    resolved = runtime or BenchRuntime.mount

    if resolved != BenchRuntime.image:
        base_image_override = None
        if image:
            if not _has_explicit_tag(image):
                raise typer.BadParameter("--image must include a tag, e.g. 'ghcr.io/acme/frappe-custom:v15'.")
            base_image_override = image
        return resolved, None, None, base_image_override

    if not image:
        raise typer.BadParameter(
            "Image deployment mode requires --image <repo:tag> -- an existing image built by 'fm bake' "
            "or otherwise present/pullable.",
        )
    if not _has_explicit_tag(image):
        raise typer.BadParameter(
            "--image must be a full reference with a tag, e.g. 'ghcr.io/acme/mybench:fm-20260722-abc123'.",
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

    repo = image.rpartition(":")[0]
    return resolved, repo, image, None


def _validate_from_image(
    from_image: str,
    resolved_runtime: BenchRuntime,
) -> None:
    """``--from-image`` contract: mount-only, explicit tag.

    ``--apps`` entries are per-app overrides grafted on top of the seed;
    ``--python``/``--node`` swap the seeded toolchain (venv recreated, apps
    reinstalled) exactly like ``fm update``.
    """
    if resolved_runtime == BenchRuntime.image:
        raise typer.BadParameter(
            "--from-image seeds a MOUNT workspace; image runtime already runs the image (use --image).",
        )
    if not _has_explicit_tag(from_image):
        raise typer.BadParameter("--from-image requires an explicit ':tag' (e.g. local/myapp:20260724-abc).")



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
    image: str | None,
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
    if "newrelic" in explicit:
        bc.newrelic_enabled = newrelic
    if "newrelic_license_key" in explicit:
        bc.newrelic_license_key = newrelic_license_key
    if not bc.db_name:
        bc.db_name = db_name

    if "apps" in explicit:
        bc.apps_list = _ensure_frappe_first(apps)
    else:
        bc.apps_list = _ensure_frappe_first(bc.apps_list)

    # Runtime/image selection: explicit --runtime/--image re-resolve (flag path);
    # otherwise keep whatever the config declared ([runtime]/[deploy]/[deploy_state]).
    if "runtime" in explicit or "image" in explicit:
        r_runtime, r_image, r_tag, r_base = _resolve_deploy_options(
            runtime if "runtime" in explicit else bc.runtime,
            image if "image" in explicit else None,
            apps if "apps" in explicit else [],
            python_version if "python_version" in explicit else None,
            node_version if "node_version" in explicit else None,
        )
        bc.runtime = r_runtime
        bc.image = r_image
        bc.base_image = r_base
        bc.deploy_state = DeployState(current_tag=r_tag) if r_runtime == BenchRuntime.image else None

    return bc, apps_from_user


@example(
    "Create bench with Frappe only",
    "{benchname}",
    detail="Creates a new bench with Frappe installed using the default stable branch. Useful for starting a minimal development environment.",
    benchname="mybench",
)
@example(
    "Create bench with ERPNext and HRMS",
    "{benchname} --apps erpnext --apps hrms",
    detail="Creates a new bench and installs ERPNext and HRMS on top of Frappe. Useful when you need these apps together.",
    benchname="mybench",
)
@example(
    "Create production bench",
    "{benchname} -e prod",
    detail="Creates a production-ready bench with production defaults (no developer tools). Use this for deployment environments.",
    benchname="mybench",
)
@example(
    "Create bench with specific branch",
    "{benchname} --apps erpnext:version-14",
    detail="Creates a bench installing ERPNext from a specific branch or tag. Use when you need a particular release.",
    benchname="mybench",
)
@example(
    "Create bench with a private app",
    "{benchname} --apps myorg/private-app --github-token ghp_xxx",
    detail="Installs a private GitHub repository by supplying a token. Keep tokens secret and prefer environment variables.",
    benchname="mybench",
)
@example(
    "Create bench with custom Python/Node versions",
    "{benchname} --python 3.11 --node 20",
    detail="Selects custom Python and Node.js versions for the bench rather than auto-detected defaults.",
    benchname="mybench",
)
@example(
    "Create bench with alias domains",
    "{benchname} --alias-domains www.example.com,api.example.com",
    detail="Adds alias domains to the bench configuration. Use 'fm ssl add' to provision certificates for these domains.",
    benchname="mybench",
)
def create(
    ctx: typer.Context,
    benchname: Annotated[str, typer.Argument(help="Bench name")],
    apps: Annotated[
        list[str],
        typer.Option(
            "--apps",
            "-a",
            help="Apps to install. Format: appname:branch or appname (e.g., erpnext:version-15)",
            callback=apps_list_validation_callback,
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = [],
    environment: Annotated[
        FMBenchEnvType,
        typer.Option("--environment", "-e", help="Environment type (dev or prod)"),
    ] = FMBenchEnvType.dev,
    developer_mode: Annotated[
        EnableDisableOptionsEnum,
        typer.Option(
            help="Enable/disable developer mode (DocType edits write app files -- editable workspace only; "
            "auto-enabled on dev-environment mount benches).",
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = EnableDisableOptionsEnum.disable,
    template: Annotated[bool, typer.Option(help="Create as template bench")] = False,
    admin_pass: Annotated[
        str,
        typer.Option(help="Administrator password"),
    ] = "admin",
    alias_domains: Annotated[
        str | None,
        typer.Option(
            help="Alias domains (comma-separated). Use 'fm ssl add' for SSL.",
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
            help="Mount runtime only: GitHub token for cloning private app repos (or use GITHUB_TOKEN env var).",
            envvar="GITHUB_TOKEN",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    python_version: Annotated[
        str | None,
        typer.Option(
            "--python",
            help="Python version (e.g., '3.11'). Auto-detected by default.",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    node_version: Annotated[
        str | None,
        typer.Option(
            "--node",
            help="Node version (e.g., '18', '20'). Auto-detected by default.",
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
            help="Skip domain uniqueness validation (not recommended). Allows creating benches with duplicate domains.",
            show_default=False,
            rich_help_panel=_PANEL_DOMAIN,
        ),
    ] = False,
    runtime: Annotated[
        BenchRuntime | None,
        typer.Option(
            "--runtime",
            help="Runtime: 'mount' (default, live-mounted editable code) or 'image' (immutable pre-built "
            "app image; settings-only, deploys via fm switch).",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option(
            "--image",
            help="Mount runtime: override the base frappe image (repo:tag). Image runtime: the pre-built "
            "app image to run (repo:tag; must exist locally or be pullable).",
            show_default=False,
            rich_help_panel=_PANEL_RUNTIME,
        ),
    ] = None,
    from_image: Annotated[
        str | None,
        typer.Option(
            "--from-image",
            help="Mount runtime: seed the workspace from a baked app image (repo:tag) instead of "
            "cloning + installing apps -- near-instant create from a release image. --apps entries "
            "become per-app OVERRIDES on top of the image (e.g. --apps frappe:develop replaces the "
            "baked frappe); --python/--node swap the seeded toolchain (venv recreated, apps reinstalled).",
            show_default=False,
            rich_help_panel=_PANEL_MOUNT,
        ),
    ] = None,
    config: Annotated[
        list[str],
        typer.Option(
            "--config",
            help="TOML config overlay: a file path or inline TOML content used as the base bench config. "
            "Explicit CLI flags override it; repeatable, later --config wins.",
            show_default=False,
        ),
    ] = [],
    newrelic: Annotated[
        bool,
        typer.Option(
            "--newrelic/--no-newrelic",
            help="Enable NewRelic APM monitoring for the web process.",
            show_default=False,
            rich_help_panel=_PANEL_MONITORING,
        ),
    ] = False,
    newrelic_license_key: Annotated[
        str | None,
        typer.Option(
            "--newrelic-license-key",
            help="NewRelic ingest license key. Required when --newrelic is set.",
            show_default=False,
            rich_help_panel=_PANEL_MONITORING,
        ),
    ] = None,
):
    """
    Create a new bench with apps.

    Creates a bench directory, config, and installs requested apps. If not specified, Frappe is included by default.

    Runtime (--runtime): 'mount' (default) live-mounts code for local development, and --image overrides the base frappe image. 'image' runs a pre-built app image (built by `fm bake` or otherwise present/pullable) given via --image and does not accept --apps/--python/--node -- those are baked into the image.

    --config supplies a TOML base (file or inline) for the bench config (e.g. [switch],
    [registry], [deploy], [build], [monitoring], per-app hooks); explicit CLI flags
    override it. Repeatable, later --config wins.
    
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
    db_name = f"fm_{sanitized_bench_name}_{secrets.token_hex(8)}"

    if config:
        explicit = {
            name
            for name in (
                "environment", "developer_mode", "admin_pass", "alias_domains", "github_token",
                "python_version", "node_version", "restart", "newrelic", "newrelic_license_key",
                "runtime", "image", "apps",
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
                image=image,
                db_name=db_name,
                explicit=explicit,
            )
        except ConfigOverlayError as e:
            output.display_error(str(e))
            raise typer.Exit(1) from e

        if bench_config.runtime == BenchRuntime.image:
            current_tag = bench_config.deploy_state.current_tag if bench_config.deploy_state else None
            if not current_tag:
                output.display_error(
                    "Image runtime needs a pre-built image: pass --image <repo:tag>, or set "
                    "top-level image + [deploy_state].current_tag in --config.",
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
        final_apps_list = apps_config if from_image else _ensure_frappe_first(apps_config)

        # Deploy model (#323): resolve runtime (mount|image) + mode-scoped --image.
        resolved_runtime, image_repo, deploy_current_tag, base_image_override = _resolve_deploy_options(
            runtime, image, apps, python_version, node_version
        )
        if from_image:
            _validate_from_image(from_image, resolved_runtime)
            output.print(
                f"Mount bench: seeding workspace from baked image [fm.info]{from_image}[/fm.info].",
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
            db_name=db_name,
            admin_tools_username=None,
            admin_tools_password=None,
            restart_policy=restart,
            newrelic_enabled=newrelic,
            newrelic_license_key=newrelic_license_key,
            runtime=resolved_runtime,
            image=image_repo,
            base_image=base_image_override,
            seed_image=from_image,
            deploy_state=DeployState(current_tag=deploy_current_tag)
            if resolved_runtime == BenchRuntime.image
            else None,
        )
        apps_from_user = bool(apps)

    # --- shared validation + creation (both paths) ---
    if bench_config.newrelic_enabled and not bench_config.newrelic_license_key:
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
