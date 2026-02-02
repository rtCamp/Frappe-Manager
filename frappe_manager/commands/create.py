from typing import Annotated, List, Optional, cast
import typer
import secrets
from frappe_manager.logger.context import LoggerContext
from frappe_manager.output_manager import spinner, get_global_output_handler
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
from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
)
from frappe_manager.metadata_manager import FMConfigManager


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
    Create a new bench with apps
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
                f"\n⚠️  {validation_result.failure_count}/{len(apps_config)} repositories failed validation"
            )
            output.display_error("Please check the repository names, branches, and authentication")
            raise typer.Exit(1)

    # Warn if prod bench is being created with restart: no
    if restart == RestartPolicyEnum.no and environment == FMBenchEnvType.prod:
        output.warning("⚠️  Creating production bench with restart policy 'no'")
        output.warning("    Containers will not auto-recover from failures or system reboots")

    with spinner(output, "Creating bench"):
        bench_service.create_bench(benchname, bench_config, is_template=template)
