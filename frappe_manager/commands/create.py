from typing import Annotated, List, Optional, cast
import typer
import secrets
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import spinner
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType, AppConfig, RestartPolicyEnum
from frappe_manager.site_manager.domain_conflict import validate_domains_unique, DomainConflictError
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.modules.app_cloner import AppCloner
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.utils.site import validate_sitename
from frappe_manager.utils.callbacks import (
    apps_list_validation_callback,
    alias_domains_validation_callback,
)
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
)
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.commands import get_output_handler


def create(
    ctx: typer.Context,
    benchname: Annotated[str, typer.Argument(help="Bench name")],
    apps: Annotated[
        List[str],
        typer.Option(
            "--apps",
            "-a",
            help="Apps to install. Format: appname:branch or appname (e.g., erpnext:version-15)",
            callback=apps_list_validation_callback,
            show_default=False,
        ),
    ] = [],
    environment: Annotated[
        FMBenchEnvType, typer.Option("--environment", "-e", help="Environment type (dev or prod)")
    ] = FMBenchEnvType.dev,
    developer_mode: Annotated[
        EnableDisableOptionsEnum, typer.Option(help="Enable/disable developer mode")
    ] = EnableDisableOptionsEnum.disable,
    template: Annotated[bool, typer.Option(help="Create as template bench")] = False,
    admin_pass: Annotated[
        str,
        typer.Option(help="Administrator password"),
    ] = "admin",
    alias_domains: Annotated[
        Optional[str],
        typer.Option(
            help="Alias domains (comma-separated). Use 'fm ssl add' for SSL.",
            callback=alias_domains_validation_callback,
            show_default=False,
        ),
    ] = None,
    github_token: Annotated[
        Optional[str],
        typer.Option(
            "--github-token",
            "-t",
            help="GitHub token for private repos (or use GITHUB_TOKEN env var)",
            envvar="GITHUB_TOKEN",
            show_default=False,
        ),
    ] = None,
    python_version: Annotated[
        Optional[str],
        typer.Option(
            "--python",
            help="Python version (e.g., '3.11'). Auto-detected by default.",
            show_default=False,
        ),
    ] = None,
    node_version: Annotated[
        Optional[str],
        typer.Option(
            "--node",
            help="Node version (e.g., '18', '20'). Auto-detected by default.",
            show_default=False,
        ),
    ] = None,
    restart: Annotated[
        Optional[RestartPolicyEnum],
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
):
    """
    Create a new bench with apps.

    Examples:

        fm create mybench
        fm create mybench --apps erpnext:version-15 --apps hrms
        fm create mybench --environment prod
        fm create mybench --apps myorg/private-app:main --github-token ghp_xxx
    """

    services_manager: ServicesManager = ctx.obj["services"]
    verbose = ctx.obj['verbose']
    fm_config: FMConfigManager = ctx.obj['fm_config_manager']

    benchname = validate_sitename(benchname)

    all_domains = {benchname}
    if alias_domains:
        all_domains.update(alias_domains)

    skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness

    try:
        validate_domains_unique(all_domains, benches_root=CLI_BENCHES_DIRECTORY, skip_check=skip_check)
    except DomainConflictError as e:
        richprint.error(str(e))
        richprint.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
        raise typer.Exit(1)

    context = LoggerContext(bench=benchname, operation="create")
    output = get_output_handler(ctx, context=context)
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
    apps_config = cast(List[AppConfig], apps)

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
    )

    # Validate repositories exist BEFORE creating any infrastructure
    # This prevents failed bench creation due to invalid repos
    if apps:
        apps_config = bench_config.get_apps_config()

        with spinner(output, f"Validating {len(apps_config)} app repositories"):
            valid, errors = AppCloner.validate_repos_exist(apps_config, github_token)

        if not valid:
            output.display_error("Repository validation failed:")
            for error in errors:
                output.display_error(f"  {error}")
            output.display_error("\nPlease check the repository names, branches, and authentication")
            output.display_error("For private repos, use --github-token or set GITHUB_TOKEN environment variable")
            raise typer.Exit(1)

        output.print(f"✓ Validated {len(apps_config)} app repositories")

    # Warn if prod bench is being created with restart: no
    if restart == RestartPolicyEnum.no and environment == FMBenchEnvType.prod:
        output.warning("⚠️  Creating production bench with restart policy 'no'")
        output.warning("    Containers will not auto-recover from failures or system reboots")

    with spinner(output, "Creating bench"):
        bench_service.create_bench(benchname, bench_config, is_template=template)
