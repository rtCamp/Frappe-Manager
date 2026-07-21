import secrets
from typing import Annotated, cast

import typer
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
    DeployBuildConfig,
    DeployConfig,
    DeploymentMode,
    FMBenchEnvType,
    RegistryConfig,
    RestartPolicyEnum,
)
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.utils.callbacks import (
    alias_domains_validation_callback,
    apps_list_validation_callback,
)
from frappe_manager.utils.site import validate_sitename


def _resolve_deploy_options(
    deployment_mode: DeploymentMode | None,
    image: str | None,
    registry: str | None,
    distribution: str,
    python_version: str | None,
    node_version: str | None,
) -> tuple[DeploymentMode, DeployConfig | None, DeployBuildConfig | None, RegistryConfig | None]:
    """Resolve the two-axis deploy model (#323) for ``fm create``.

    Image mode is opt-in — chosen explicitly (``--deployment-mode image``) or
    implied by ``--image`` — so plain ``fm create`` (and ``--environment prod``)
    keep producing backward-compatible ``mount`` benches. Returns the resolved
    ``deployment_mode`` plus the ``[deploy]``/``[build]``/``[registry]`` configs
    (``None`` in mount mode).
    """
    resolved = deployment_mode or (DeploymentMode.image if image else DeploymentMode.mount)

    if resolved != DeploymentMode.image:
        if image:
            raise typer.BadParameter("--image is only valid in image deployment mode (use --deployment-mode image).")
        if registry:
            raise typer.BadParameter("--registry is only valid in image deployment mode.")
        return resolved, None, None, None

    if not image:
        raise typer.BadParameter(
            "Image deployment mode requires --image <repo> (fm bake/deploy pin its tag as <repo>:<timestamp>-<sha>).",
        )
    if registry and distribution not in ("registry", "save_load"):
        raise typer.BadParameter("--distribution must be 'registry' or 'save_load'.")

    deploy_config = DeployConfig(image=image)
    build_config = (
        DeployBuildConfig(python_version=python_version, node_version=node_version)
        if (python_version or node_version)
        else None
    )
    registry_config = RegistryConfig(registry=registry, distribution=distribution) if registry else None
    return resolved, deploy_config, build_config, registry_config


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
        ),
    ] = [],
    environment: Annotated[
        FMBenchEnvType,
        typer.Option("--environment", "-e", help="Environment type (dev or prod)"),
    ] = FMBenchEnvType.dev,
    developer_mode: Annotated[
        EnableDisableOptionsEnum,
        typer.Option(help="Enable/disable developer mode"),
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
        ),
    ] = None,
    github_token: Annotated[
        str | None,
        typer.Option(
            "--github-token",
            "-t",
            help="GitHub token for private repos (or use GITHUB_TOKEN env var)",
            envvar="GITHUB_TOKEN",
            show_default=False,
        ),
    ] = None,
    python_version: Annotated[
        str | None,
        typer.Option(
            "--python",
            help="Python version (e.g., '3.11'). Auto-detected by default.",
            show_default=False,
        ),
    ] = None,
    node_version: Annotated[
        str | None,
        typer.Option(
            "--node",
            help="Node version (e.g., '18', '20'). Auto-detected by default.",
            show_default=False,
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
        ),
    ] = False,
    newrelic: Annotated[
        bool,
        typer.Option(
            "--newrelic/--no-newrelic",
            help="Enable NewRelic APM monitoring for the web process.",
            show_default=False,
        ),
    ] = False,
    newrelic_license_key: Annotated[
        str | None,
        typer.Option(
            "--newrelic-license-key",
            help="NewRelic ingest license key. Required when --newrelic is set.",
            show_default=False,
        ),
    ] = None,
    deployment_mode: Annotated[
        DeploymentMode | None,
        typer.Option(
            "--deployment-mode",
            help="Runtime: 'mount' (live-mounted code, dev) or 'image' (immutable app image, prod). "
            "Defaults to 'image' when --image is given, else 'mount'.",
            show_default=False,
        ),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option(
            "--image",
            help="Image repository for image-based deployment (sets [deploy].image; fm manages the :tag). "
            "Implies image deployment mode.",
            show_default=False,
        ),
    ] = None,
    registry: Annotated[
        str | None,
        typer.Option(
            "--registry",
            help="Registry host/namespace for image push/pull (sets [registry].registry). Image mode only.",
            show_default=False,
        ),
    ] = None,
    distribution: Annotated[
        str,
        typer.Option(
            "--distribution",
            help="Image transport when a registry is set: 'registry' (push/pull) or 'save_load' (docker save/load over SSH).",
        ),
    ] = "registry",
):
    """
    Create a new bench with apps.

    Creates a bench directory, config, and installs requested apps. If not specified, Frappe is included by default.
    """

    services_manager: ServicesManager = ctx.obj["services"]
    verbose = ctx.obj["verbose"]
    fm_config: FMConfigManager = ctx.obj["fm_config_manager"]

    benchname = validate_sitename(benchname)

    if newrelic and not newrelic_license_key:
        raise typer.BadParameter("--newrelic-license-key is required when --newrelic is set.")

    all_domains = {benchname}
    if alias_domains:
        all_domains.update(alias_domains)

    skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness

    try:
        validate_domains_unique(all_domains, benches_root=CLI_BENCHES_DIRECTORY, skip_check=skip_check)
    except DomainConflictError as e:
        output = get_global_output_handler()
        output.display_error(str(e))
        output.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
        raise typer.Exit(1)

    output = get_global_output_handler()
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    bench_path = bench_service.benches_directory / benchname
    bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME

    if developer_mode == EnableDisableOptionsEnum.enable:
        developer_mode_status = True
    elif developer_mode == EnableDisableOptionsEnum.disable:
        developer_mode_status = False

    # Ensure frappe is always first in apps_list
    # If user didn't specify frappe, add default version
    # If user specified frappe, move it to first position

    # Callback returns List[AppConfig], cast for type checker
    apps_config = cast("list[AppConfig]", apps)

    final_apps_list = []
    frappe_app = None
    other_apps = []

    for app_config in apps_config:
        if app_config.name == "frappe" or app_config.name.endswith("/frappe"):
            frappe_app = app_config
        else:
            other_apps.append(app_config)

    if frappe_app is None:
        frappe_app = AppConfig.from_string(f"frappe:{STABLE_APP_BRANCH_MAPPING_LIST['frappe']}")

    final_apps_list = [frappe_app] + other_apps

    sanitized_bench_name = benchname.replace(".", "_").replace("-", "_")
    db_name = f"fm_{sanitized_bench_name}_{secrets.token_hex(8)}"

    # Two-axis deploy model (#323): resolve runtime (mount|image) + image configs.
    resolved_deployment_mode, deploy_config, build_config, registry_config = _resolve_deploy_options(
        deployment_mode, image, registry, distribution, python_version, node_version
    )
    if resolved_deployment_mode == DeploymentMode.image:
        output.print(
            f"Image bench: provisions apps + creates the site now; run "
            f"[blue]fm deploy {benchname}[/blue] to bake the image and switch to it.",
            emoji_code=":package:",
        )

    bench_config: BenchConfig = BenchConfig(
        name=benchname,
        apps_list=final_apps_list,
        developer_mode=True if environment == FMBenchEnvType.dev else developer_mode_status,
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
        deployment_mode=resolved_deployment_mode,
        deploy=deploy_config,
        build=build_config,
        registry=registry_config,
    )

    if apps:
        apps_config = bench_config.get_apps_config()

        with spinner(output, f"Validating {len(apps_config)} app repositories"):
            validation_result = AppConfig.validate_repos_batch(apps_config, github_token)

        for result in validation_result.results:
            if result.success:
                output.print(result.display_message, emoji_code=":white_check_mark:")
            else:
                output.display_error(result.display_message, emoji_code=":cross_mark:")

        if not validation_result.all_valid:
            output.display_error(
                f"\n⚠️  {validation_result.failure_count}/{len(apps_config)} repositories failed validation",
            )
            output.display_error("Please check the repository names, branches, and authentication")
            raise typer.Exit(1)

    # Warn if prod bench is being created with restart: no
    if restart == RestartPolicyEnum.no and environment == FMBenchEnvType.prod:
        output.warning("⚠️  Creating production bench with restart policy 'no'")
        output.warning("    Containers will not auto-recover from failures or system reboots")

    with spinner(output, "Creating bench"):
        bench_service.create_bench(benchname, bench_config, is_template=template)
