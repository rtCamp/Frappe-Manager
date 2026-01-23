from pathlib import Path
from rich.panel import Panel
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.utils.site import pull_docker_images, validate_sitename
from frappe_manager.site_manager.domain_conflict import validate_domains_unique, DomainConflictError
import typer
import os
import sys
import shutil
import secrets
from typing import Annotated, List, Optional
from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.ngrok import create_tunnel
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.modules.app_cloner import AppCloner
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.output_manager import spinner, temporary_stop
from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_DIR,
    DEFAULT_EXTENSIONS,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
    SiteServicesEnum,
    CLI_BENCHES_DIRECTORY,
    CLI_FM_CONFIG_PATH,
)
from frappe_manager.docker import DockerClient
from frappe_manager.logger import log
from frappe_manager.logger.context import LoggerContext
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor, needs_migration, needs_system_migration, get_benches_needing_migration
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    apps_list_validation_callback,
    create_command_sitename_callback,
    sites_autocompletion_callback,
    version_callback,
    sitename_callback,
    code_command_extensions_callback,
    alias_domains_validation_callback,
)
from frappe_manager.utils.helpers import (
    is_cli_help_called,
    get_current_fm_version,
)
from frappe_manager.services_manager.commands import services_root_command
from frappe_manager.sub_commands.self_commands import self_app
from frappe_manager.sub_commands.ssl_command import ssl_root_command
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType, RestartPolicyEnum
from frappe_manager.migration_manager.version import Version
from frappe_manager.docker import ComposeFile
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


def check_bench_migration_required(bench_name: Optional[str]) -> None:
    from frappe_manager.migration_manager.bench_migration_state import bench_needs_migration
    
    if not bench_name:
        return
    
    bench_path = CLI_BENCHES_DIRECTORY / bench_name
    
    if not bench_path.exists():
        return
    
    current_version = Version(get_current_fm_version())
    
    if bench_needs_migration(bench_path, current_version):
        if richprint.live:
            richprint.live.stop()
        panel = Panel(
            f"[yellow]⚠️  Bench Migration Required[/yellow]\n\n"
            f"Bench '[bold]{bench_name}[/bold]' needs migration.\n\n"
            f"[bold cyan]fm migrate {bench_name}[/bold cyan]",
            border_style="yellow",
            title=f"Bench: {bench_name}",
        )
        richprint.stdout.print(panel)
        raise typer.Exit(0)
app.add_typer(services_root_command, name="services", help="Handle global services.")
app.add_typer(self_app, name="self", help="Perform operations related to the [bold][blue]fm[/bold][/blue] itself.")
app.add_typer(ssl_root_command, name="ssl", help="Perform operations related to ssl.")


def get_output_handler(ctx: typer.Context, context: Optional[LoggerContext] = None) -> OutputHandler:
    """
    Create output handler based on context settings.

    This function creates a LoggingOutputHandler that wraps RichOutputHandler,
    automatically logging all user-facing output to the file logger with optional context.

    Args:
        ctx: Typer context with log_level and verbose settings
        context: Optional LoggerContext for contextual logging (bench, operation, etc.)

    Returns:
        LoggingOutputHandler wrapping RichOutputHandler with contextual logging
    """
    from frappe_manager.logger import ContextualLogger

    verbose = ctx.obj.get("verbose", False)

    rich = RichOutputHandler(verbose=verbose)

    base_logger = log.get_logger()

    contextual_logger = ContextualLogger(base_logger, context)

    output = LoggingOutputHandler(rich, contextual_logger)

    return output


@app.callback()
def app_callback(
    ctx: typer.Context,
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="Increase verbosity (-v for info, -vv for debug)")
    ] = 0,
    log_level: Annotated[
        Optional[str],
        typer.Option("--log-level", help="Set log level explicitly (debug|info|warning|error)"),
    ] = None,
    version: Annotated[
        Optional[bool], typer.Option("--version", "-V", help="Show Version.", callback=version_callback)
    ] = None,
):
    """
    Docker Compose based CLI for managing Frappe benches.

    Create, manage, and develop isolated Frappe environments using containers.
    Each bench runs independently with its own apps, database, and configuration.
    """
    ctx.obj = {}

    # Determine effective log level
    if log_level:
        # Explicit --log-level takes precedence
        level_name = log_level.upper()

        # Validate log level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        if level_name not in valid_levels:
            richprint.error(f"Invalid log level: {log_level}. Must be one of: {', '.join(valid_levels).lower()}")
            raise typer.Exit(1)
    else:
        # Map -v count to log level
        level_map = {
            0: "WARNING",  # Default
            1: "INFO",  # -v
            2: "DEBUG",  # -vv or more
        }
        level_name = level_map.get(min(verbose, 2), "WARNING")

    # Store in context for commands
    ctx.obj["log_level"] = level_name
    ctx.obj["verbose"] = level_name in ["INFO", "DEBUG"]

    help_called = is_cli_help_called(ctx)
    ctx.obj["is_help_called"] = help_called

    if not help_called:
        with spinner(richprint, "Working"):
            if not CLI_DIR.exists():
                CLI_DIR.mkdir(parents=True, exist_ok=True)
                CLI_BENCHES_DIRECTORY.mkdir(parents=True, exist_ok=True)
                richprint.print(f"fm directory doesn't exists! Created at -> {str(CLI_DIR)}")
            elif not CLI_DIR.is_dir():
                richprint.exit("Sites directory is not a directory! Aborting!")

            global logger
            console_level = level_name if ctx.obj["verbose"] else None
            logger = log.get_logger(console_level=console_level)

            import logging

            logger.setLevel(getattr(logging, level_name))

            logger.info("")
            logger.info(f"{':' * 20}FM Invoked{':' * 20}")
            logger.info("")

            logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
            logger.info(f"LOG LEVEL: {level_name}")
            logger.info("-" * 20)

            if not DockerClient().server_running():
                richprint.exit("Docker daemon not running. Please start docker service")

            fm_config_manager: FMConfigManager = FMConfigManager.import_from_toml()

            if not CLI_FM_CONFIG_PATH.exists():
                richprint.print("First installation detected. Pulling docker images...️", "🔍")

                completed_status = pull_docker_images()

                if not completed_status:
                    if CLI_DIR.exists():
                        shutil.rmtree(CLI_DIR)
                    richprint.exit("Aborting. Not able to pull all required Docker images")

                current_version = Version(get_current_fm_version())
                fm_config_manager.version = current_version
                fm_config_manager.export_to_toml()

            invoked_command = ctx.invoked_subcommand or "no-command"
            allowed_without_system = ["migrate", "version", "self"]
            
            if needs_system_migration(fm_config_manager):
                if invoked_command not in allowed_without_system:
                    if richprint.live:
                        richprint.live.stop()
                    panel = Panel(
                        "[yellow]⚠️  FM System Migration Required[/yellow]\n\n"
                        "Frappe Manager core needs migration.\n"
                        "This updates global config and services.\n\n"
                        "[bold cyan]fm migrate --system --all-benches[/bold cyan]",
                        border_style="yellow",
                        title="Migration Required",
                    )
                    richprint.stdout.print(panel)
                    raise typer.Exit(0)

            services_manager: ServicesManager = ServicesManager(
                verbose=ctx.obj["verbose"],
                invoked_subcommand=ctx.invoked_subcommand,
            )

            services_manager.init()

            try:
                services_manager.entrypoint_checks(start=True)
            except ServicesNotCreated as e:
                services_manager.remove_itself()
                richprint.exit(f"Not able to create services. {e}")

        ctx.obj["services"] = services_manager
        ctx.obj['fm_config_manager'] = fm_config_manager


@app.command(no_args_is_help=True)
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
    final_apps_list = []
    frappe_app = None
    other_apps = []

    for app_dict in apps:
        app_name = app_dict.get("app", "")
        # Check if this is the frappe app (exact match or org/frappe format)
        if app_name == "frappe" or app_name.endswith("/frappe"):
            frappe_app = app_dict
        else:
            other_apps.append(app_dict)

    # If frappe not specified, add default version
    if frappe_app is None:
        frappe_app = {"app": "frappe", "branch": STABLE_APP_BRANCH_MAPPING_LIST['frappe']}

    # Build final apps list: frappe first, then others in order
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
        output.info("Validating app repositories..")
        apps_config = bench_config.get_apps_config()
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


@app.command()
def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation prompts")] = False,
    delete_db_from_global_db: Annotated[
        Optional[bool],
        typer.Option(
            "--delete-db-from-global-db/--no-delete-db-from-global-db",
            help="Delete database from global-db service",
        ),
    ] = None,
):
    """
    Delete a bench.

    Examples:

        fm delete mybench
        fm delete mybench --force
        fm delete mybench --delete-db-from-global-db
    """
    
    check_bench_migration_required(benchname)

    if benchname:
        services_manager = ctx.obj["services"]
        verbose = ctx.obj['verbose']

        # Create context for this operation
        context = LoggerContext(bench=benchname, operation="delete")
        output = get_output_handler(ctx, context=context)
        bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
        bench_service.delete_bench(benchname, force=force, delete_db_from_global_db=delete_db_from_global_db)


@app.command()
def list(ctx: typer.Context):
    """
    List all benches.

    Examples:

        fm list
    """

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create context for this operation
    context = LoggerContext(operation="list")
    output = get_output_handler(ctx, context=context)
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    table = bench_service.list_benches_table()
    if table.row_count:
        output.print_data(table)


@app.command()
def start(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Recreate containers")] = False,
    sync_bench_config_changes: Annotated[bool, typer.Option("--sync-config", help="Sync config changes")] = False,
    reconfigure_supervisor: Annotated[
        bool, typer.Option("--reconfigure-supervisor", help="Reconfigure supervisor")
    ] = False,
    reconfigure_common_site_config: Annotated[
        bool, typer.Option("--reconfigure-common-site-config", help="Reconfigure site config")
    ] = False,
    reconfigure_workers: Annotated[bool, typer.Option("--reconfigure-workers", help="Reconfigure workers")] = False,
    include_default_workers: Annotated[bool, typer.Option(help="Include default workers")] = True,
    include_custom_workers: Annotated[bool, typer.Option(help="Include custom workers")] = True,
    sync_dev_packages: Annotated[bool, typer.Option("--sync-dev-packages", help="Sync dev packages")] = False,
):
    """
    Start a bench.

    Examples:

        fm start mybench
        fm start mybench --force
        fm start mybench --sync-config
    """
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="start")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    bench.start(
        force=force,
        sync_bench_config_changes=sync_bench_config_changes,
        reconfigure_workers=reconfigure_workers,
        include_default_workers=include_default_workers,
        include_custom_workers=include_custom_workers,
        reconfigure_common_site_config=reconfigure_common_site_config,
        reconfigure_supervisor=reconfigure_supervisor,
        sync_dev_packages=sync_dev_packages,
    )


@app.command()
def stop(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Stop a bench."""
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="stop")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    bench.stop()


@app.command()
def code(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    user: Annotated[str, typer.Option(help="User to connect as")] = "frappe",
    extensions: Annotated[
        List[str],
        typer.Option(
            "--extension",
            "-e",
            help="VSCode extensions to install (e.g., ms-python.python)",
            callback=code_command_extensions_callback,
            show_default=False,
        ),
    ] = DEFAULT_EXTENSIONS,
    force_start: Annotated[bool, typer.Option("--force-start", "-f", help="Start bench before opening VSCode")] = False,
    debugger: Annotated[bool, typer.Option("--debugger", "-d", help="Setup debugger config")] = False,
    workdir: Annotated[
        str, typer.Option("--work-dir", "-w", help="Working directory in VSCode")
    ] = '/workspace/frappe-bench',
):
    """Open bench in vscode."""
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="code")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if force_start:
        bench.start()

    bench.attach_to_bench(user=user, extensions=extensions, workdir=workdir, debugger=debugger)


@app.command()
def logs(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    service: Annotated[Optional[str], typer.Option(help="Service name (frappe, nginx, redis-cache, etc.)")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs in real-time")] = False,
):
    """Show bench logs (server or container)"""
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="logs")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if service:
        available_services = bench.get_available_services()
        if service not in available_services:
            output.display_error(f"Service '{service}' not found")
            output.print(f"Available services: {', '.join(sorted(available_services))}")
            raise typer.Exit(1)

    bench.logs(follow, service)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def shell(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    command: Annotated[Optional[str], typer.Option("-c", "--command", help="Execute command and exit")] = None,
    user: Annotated[Optional[str], typer.Option(help="User to connect as", show_default=False)] = None,
    service: Annotated[str, typer.Option(help="Service to connect to")] = "frappe",
    shell_path: Annotated[Optional[str], typer.Option(help="Shell path (e.g., /bin/bash, /bin/sh)")] = None,
    run: Annotated[bool, typer.Option(help="Use 'docker compose run --rm'")] = False,
):
    """
    Spawn shell for the bench or execute a command.

    [bold cyan]Interactive shell mode:[/bold cyan]
      fm shell mysite              # Spawn interactive shell
      fm shell mysite --user root  # Spawn shell as root user

    [bold cyan]Command execution mode:[/bold cyan]
      fm shell mysite -c "python --version"        # Execute single command
      fm shell mysite -- python --version          # Execute command (passthrough syntax)
      fm shell mysite -- bench --version           # Run bench commands
      fm shell mysite -c "ls -la /workspace"       # Execute shell commands

    [bold cyan]Advanced options:[/bold cyan]
      fm shell mysite --shell-path /bin/sh         # Use specific shell
      fm shell mysite --run -c "python --version"  # Use docker compose run --rm

    Exit code from the executed command is preserved for scripting.
    """
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="shell")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    available_services = bench.get_available_services()
    if service not in available_services:
        output.display_error(f"Service '{service}' not found")
        output.print(f"Available services: {', '.join(sorted(available_services))}")
        raise typer.Exit(1)

    # Check if we have passthrough arguments (-- syntax)
    passthrough_args = ctx.args if ctx.args else None

    # Determine mode: interactive or command execution
    if command or passthrough_args:
        # Command execution mode
        if passthrough_args:
            # Use passthrough arguments (everything after --)
            exec_command = " ".join(passthrough_args)
        else:
            # Use -c command
            exec_command = command

        exit_code = bench.execute_command(service, exec_command, user, shell_path=shell_path, use_run=run)

        # Exit with the command's exit code
        if exit_code != 0:
            raise typer.Exit(exit_code)
    else:
        # Interactive shell mode (original behavior)
        bench.shell(service, user, shell_path=shell_path, use_run=run)


@app.command()
def info(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Show bench information and configuration"""
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="info")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    with spinner(output, "Getting bench info"):
        bench.info()


@app.command()
def update(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    admin_tools: Annotated[
        Optional[EnableDisableOptionsEnum],
        typer.Option("--admin-tools", help="Toggle admin-tools.", show_default=False),
    ] = None,
    environment: Annotated[
        Optional[FMBenchEnvType],
        typer.Option("--environment", "-e", help="Switch bench environment.", show_default=False),
    ] = None,
    developer_mode: Annotated[
        Optional[EnableDisableOptionsEnum],
        typer.Option(help="Toggle frappe developer mode.", show_default=False),
    ] = None,
    mailpit_as_default_mail_server: Annotated[
        bool,
        typer.Option(
            "--mailpit-as-default-mail-server", help="Configure Mailpit as default mail server", show_default=False
        ),
    ] = False,
    add_alias: Annotated[
        Optional[str],
        typer.Option(
            "--add-alias",
            help="Add alias domains to the site (comma-separated, e.g., www.example.com,api.example.com)",
            callback=alias_domains_validation_callback,
            show_default=False,
        ),
    ] = None,
    remove_alias: Annotated[
        Optional[str],
        typer.Option(
            "--remove-alias",
            help="Remove alias domains from the site (comma-separated, e.g., shop.example.com)",
            callback=alias_domains_validation_callback,
            show_default=False,
        ),
    ] = None,
    upload_limit: Annotated[
        Optional[str],
        typer.Option(
            "--upload-limit",
            help="Set maximum upload size for files (e.g., '50M', '100M', '500M', '1G')",
            show_default=False,
        ),
    ] = None,
    python_version: Annotated[
        Optional[str],
        typer.Option(
            "--python",
            help="Update Python version (e.g., '3.11', '3.12', '>=3.11,<3.14'). Will recreate virtual environment.",
            show_default=False,
        ),
    ] = None,
    node_version: Annotated[
        Optional[str],
        typer.Option(
            "--node",
            help="Update Node version (e.g., '18', '20', '>=18'). Will install and set as default.",
            show_default=False,
        ),
    ] = None,
    skip_version_check: Annotated[
        bool,
        typer.Option(
            "--skip-version-check",
            help="Skip validation of Python/Node versions against Frappe requirements. Use with caution.",
            show_default=False,
        ),
    ] = False,
    restart: Annotated[
        Optional[RestartPolicyEnum],
        typer.Option(
            "--restart",
            help="Update Docker restart policy for all bench services.",
            show_default=False,
        ),
    ] = None,
    allow_domain_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-domain-conflicts",
            help="Skip domain uniqueness validation when adding aliases (not recommended).",
            show_default=False,
        ),
    ] = False,
):
    """Update bench configuration and settings"""

    services_manager = ctx.obj["services"]
    fm_config: FMConfigManager = ctx.obj['fm_config_manager']

    context = LoggerContext(bench=benchname, operation="update")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    bench_config_save = False

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    if developer_mode:
        if developer_mode == EnableDisableOptionsEnum.enable:
            bench.bench_config.developer_mode = True
            richprint.print("Enabling frappe developer mode")
            bench.set_common_bench_config({'developer_mode': bench.bench_config.developer_mode})
            richprint.print("Enabled frappe developer mode")
        elif developer_mode == EnableDisableOptionsEnum.disable:
            bench.bench_config.developer_mode = False
            richprint.print("Disabling frappe developer mode")
            bench.set_common_bench_config({'developer_mode': bench.bench_config.developer_mode})
            richprint.print("Enabled frappe developer mode")

        bench_config_save = True

    if environment:
        richprint.change_head(f"Switching bench environemnt to {environment.value}")
        bench.bench_config.environment_type = environment

        bench.generate_compose(bench.bench_config.export_to_compose_inputs())

        richprint.print("Recreating containers to apply environment change..")
        bench.docker_client.compose.up(detach=True, force_recreate=True, stream=False)

        richprint.print("Configuring supervisor for new environment..")
        bench.switch_bench_env()

        richprint.print(f"Switched bench environemnt to {environment.value}")
        bench_config_save = True

    if restart:
        old_policy = bench.bench_config.restart_policy.value
        if restart != bench.bench_config.restart_policy:
            richprint.change_head(f"Updating restart policy from '{old_policy}' to '{restart.value}'")

            if restart == RestartPolicyEnum.no and bench.bench_config.environment_type == FMBenchEnvType.prod:
                richprint.warning("⚠️  Setting restart policy to 'no' on production bench")
                richprint.warning("    Containers will not auto-recover from failures or system reboots")

            bench.bench_config.restart_policy = restart
            bench.generate_compose(bench.bench_config.export_to_compose_inputs())

            if bench.workers.compose_file_manager.compose_path.exists():
                bench.workers.generate_compose()

            if bench.admin_tools.compose_file_manager.compose_path.exists():
                db_host = bench.services.database_manager.database_server_info.host
                bench.admin_tools.generate_compose(db_host)

            richprint.print("Restarting containers to apply restart policy..")
            bench.docker_client.compose.up(detach=True, force_recreate=True, stream=False)

            richprint.print(f"Updated restart policy to '{restart.value}'")
            bench_config_save = True
        else:
            richprint.print(f"Restart policy is already set to '{restart.value}'")

    if admin_tools:
        if admin_tools == EnableDisableOptionsEnum.enable:
            richprint.change_head("Enabling Admin-tools")
            bench.bench_config.admin_tools = True

            if not bench.admin_tools.compose_file_manager.compose_path.exists():
                bench.sync_admin_tools_compose()
            else:
                bench.admin_tools.enable(force_configure=mailpit_as_default_mail_server)

            bench_config_save = True
            richprint.print("Enabled Admin-tools")

        elif admin_tools == EnableDisableOptionsEnum.disable:
            if not bench.admin_tools.compose_file_manager.compose_path.exists() or not bench.bench_config.admin_tools:
                richprint.print("Admin tools is already disabled")
                return
            else:
                bench.bench_config.admin_tools = False
                bench.admin_tools.disable()
                bench_config_save = True

    if add_alias or remove_alias:
        add_domains_list = add_alias if add_alias else []
        remove_domains_list = remove_alias if remove_alias else []

        if add_domains_list:
            skip_check = allow_domain_conflicts or not fm_config.validation.enforce_domain_uniqueness

            try:
                validate_domains_unique(
                    add_domains_list,
                    benches_root=CLI_BENCHES_DIRECTORY,
                    exclude_bench=bench.name,
                    skip_check=skip_check,
                )
            except DomainConflictError as e:
                richprint.error(str(e))
                richprint.print("\nTo proceed anyway, use: --allow-domain-conflicts", emoji_code="")
                raise typer.Exit(1)

        richprint.change_head("Updating alias domains")
        bench.update_alias_domains(add_domains=add_domains_list, remove_domains=remove_domains_list)
        richprint.print("Alias domains updated successfully")

    # Handle upload limit update
    if upload_limit:
        richprint.change_head(f"Updating upload size limit to {upload_limit}")
        bench.update_upload_limit(upload_limit)

    if python_version or node_version:
        from frappe_manager.site_manager.bench_config import (
            extract_python_version_requirement,
            extract_node_version_requirement,
            validate_python_version_compatibility,
            validate_node_version_compatibility,
        )

        frappe_app_path = bench.path / "workspace" / "frappe-bench" / "apps" / "frappe"

        if python_version or node_version:
            current_versions = bench.app_manager.get_current_runtime_versions(use_run=True)

        if python_version:
            old_python = current_versions.get('python') or "not set"

            if frappe_app_path.exists() and not skip_version_check:
                frappe_python_req = extract_python_version_requirement(frappe_app_path)
                if frappe_python_req:
                    richprint.print(f"Frappe requires Python: {frappe_python_req}")
                    richprint.print(f"User requested Python: {python_version}")

                    is_compatible, error_msg = validate_python_version_compatibility(python_version, frappe_python_req)
                    if not is_compatible:
                        richprint.error(f"❌ {error_msg}")
                        richprint.print("Use --skip-version-check to bypass this validation (not recommended)")
                        raise typer.Exit(code=1)

            bench.bench_config.python_version = python_version
            richprint.change_head("Updating Python version")
            richprint.print(f"  Old: {old_python}")
            richprint.print(f"  New: {python_version}")
            bench_config_save = True

        if node_version:
            old_node = current_versions.get('node') or "not set"

            if frappe_app_path.exists() and not skip_version_check:
                frappe_node_req = extract_node_version_requirement(frappe_app_path)
                if frappe_node_req:
                    richprint.print(f"Frappe requires Node: {frappe_node_req}")
                    richprint.print(f"User requested Node: {node_version}")

                    is_compatible, error_msg = validate_node_version_compatibility(node_version, frappe_node_req)
                    if not is_compatible:
                        richprint.error(f"❌ {error_msg}")
                        richprint.print("Use --skip-version-check to bypass this validation (not recommended)")
                        raise typer.Exit(code=1)

            bench.bench_config.node_version = node_version
            richprint.change_head("Updating Node version")
            richprint.print(f"  Old: {old_node}")
            richprint.print(f"  New: {node_version}")
            bench_config_save = True

        bench.save_bench_config()
        bench_config_save = False

        richprint.change_head("Setting up new runtime environment")
        venv_recreated = bench.app_manager.setup_python_and_node_environments(use_run=True)
        richprint.print("Runtime versions updated successfully")

        if venv_recreated:
            apps_txt_path = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"
            if apps_txt_path.exists():
                installed_apps = [line.strip() for line in apps_txt_path.read_text().splitlines() if line.strip()]
                apps_list = [{"app": app_name, "branch": None} for app_name in installed_apps]

                richprint.change_head("Reinstalling apps into new virtual environment")
                richprint.print(f"Found {len(apps_list)} installed apps: {', '.join(installed_apps)}")
                bench.app_manager.install_apps(
                    apps_list=apps_list,
                    github_token=bench.bench_config.github_token,
                    use_uv=bench.bench_config.use_uv,
                    skip_clone=True,
                    use_run=True,
                )
                richprint.print("All apps reinstalled successfully")
            else:
                richprint.warning("No apps.txt found, skipping app reinstallation")

        richprint.change_head("Restarting services to apply new runtime versions")
        richprint.print("Restarting web services (frappe, socketio)..")
        bench.restart_web_containers_services(use_container_restart=False)
        richprint.print("Restarting worker services (schedule, workers)..")
        bench.restart_workers_containers_services(use_container_restart=False)
        richprint.print("All services restarted successfully")

    if bench_config_save:
        bench.save_bench_config()


@app.command()
def reset(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    admin_pass: Annotated[
        Optional[str],
        typer.Option(help="Password for the 'Administrator' User."),
    ] = None,
):
    """Drop database and reinstall all apps"""
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="reset")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    bench.reset(admin_pass)


@app.command()
def restart(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    web: Annotated[
        bool,
        typer.Option(help="Restart web service i.e socketio and frappe server."),
    ] = True,
    workers: Annotated[
        bool,
        typer.Option(help="Restart worker services i.e schedule and all workers."),
    ] = True,
    redis: Annotated[
        bool,
        typer.Option(help="Restart redis services."),
    ] = False,
    nginx: Annotated[
        bool,
        typer.Option(help="Restart nginx service."),
    ] = False,
    container: Annotated[
        bool,
        typer.Option(
            "--container",
            help="Restart entire Docker container(s). Stops and starts the container.",
        ),
    ] = False,
    supervisor: Annotated[
        bool,
        typer.Option(
            "--supervisor",
            help="Restart supervisor processes inside container. Faster than container restart.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Force restart: --supervisor uses stop+start (kills processes), --container uses timeout=0 (immediate kill).",
        ),
    ] = False,
):
    """Restart bench services (web, workers, redis, nginx)"""
    
    check_bench_migration_required(benchname)

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    context = LoggerContext(bench=benchname, operation="restart")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    use_container_restart = container
    use_supervisor_restart = supervisor

    if not use_container_restart and not use_supervisor_restart:
        use_supervisor_restart = True

    if use_container_restart and use_supervisor_restart:
        output.error("Cannot use both --container and --supervisor flags simultaneously", exception=typer.Exit(code=1))

    if web:
        bench.restart_web_containers_services(use_container_restart=use_container_restart, force=force)

    if workers:
        bench.restart_workers_containers_services(use_container_restart=use_container_restart, force=force)

    if redis:
        bench.restart_redis_services_containers()

    if nginx:
        bench.restart_nginx_service(force=force)


@app.command()
def ngrok(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        typer.Option("--auth-token", "-t", help="Ngrok authentication token", envvar="NGROK_AUTHTOKEN"),
    ] = None,
):
    """Create ngrok tunnel for bench"""
    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    context = LoggerContext(bench=benchname, operation="ngrok")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]

    with spinner(richprint, "Setting up ngrok tunnel"):
        if not auth_token and fm_config_manager.ngrok_auth_token:
            auth_token = fm_config_manager.ngrok_auth_token
            richprint.print("Using ngrok auth token from config file", emoji_code=":key:")
        elif not auth_token:
            richprint.exit(
                "Ngrok auth token is required. Please provide it with --auth-token or set NGROK_AUTHTOKEN environment variable."
            )

        if auth_token and not fm_config_manager.ngrok_auth_token:
            richprint.print("New auth token provided", emoji_code=":new:")

            with temporary_stop(richprint):
                should_save = richprint.prompt_ask(
                    prompt="Do you want to save the ngrok auth token in config for future use?",
                    choices=['yes', 'no'],
                )

            if should_save == 'yes':
                richprint.print("Saving auth token to config...", emoji_code=":floppy_disk:")
                fm_config_manager.ngrok_auth_token = auth_token
                fm_config_manager.export_to_toml()
                richprint.print("Saved ngrok auth token to config", emoji_code=":white_check_mark:")

        richprint.print(f"Creating ngrok tunnel for {bench.name}", emoji_code=":link:")

        try:
            create_tunnel(bench.name, auth_token)
        except Exception as e:
            richprint.error(f"Failed to create tunnel: {str(e)}")
            raise


@app.command()
def migrate(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(help="Bench name to migrate"),
    ] = None,
    system: Annotated[
        bool,
        typer.Option("--system", help="Migrate system (FM config and global services)"),
    ] = False,
    all_benches: Annotated[
        bool,
        typer.Option("--all-benches", help="Migrate all benches"),
    ] = False,
    skip_backup: Annotated[
        bool,
        typer.Option("--skip-backup", help="Skip all backups (DANGEROUS - use only if backups fail)"),
    ] = False,
    skip_backup_for: Annotated[
        Optional[str],
        typer.Option("--skip-backup-for", help="Skip backup for specific benches (comma-separated)"),
    ] = None,
    exclude_bench: Annotated[
        Optional[str],
        typer.Option("--exclude-bench", help="Exclude specific benches from migration (only with --all-benches)"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip all confirmation prompts"),
    ] = False,
):
    """
    Migrate Frappe Manager to current version.
    
    Migration operates at two levels:
    - System: FM config and global services (opt-in with --system)
    - Benches: Individual bench environments (specify explicitly)
    
    Examples:
    
      # Migrate system only
      fm migrate --system
      
      # Migrate specific bench only
      fm migrate mybench
      
      # Migrate system + specific bench
      fm migrate --system mybench
      
      # Migrate all benches only
      fm migrate --all-benches
      
      # Migrate system + all benches
      fm migrate --system --all-benches
      
      # Migrate all benches except specific ones
      fm migrate --all-benches --exclude-bench old-bench,test-bench
      
      # Skip backup for benches where backup fails
      fm migrate --all-benches --skip-backup-for bench1,bench2
    """
    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]
    
    if benchname and all_benches:
        richprint.error("Cannot specify both <benchname> and --all-benches")
        raise typer.Exit(1)
    
    if exclude_bench and not all_benches:
        richprint.error("--exclude-bench can only be used with --all-benches")
        raise typer.Exit(1)
    
    if not system and not benchname and not all_benches:
        richprint.error("Must specify what to migrate: --system, <benchname>, or --all-benches")
        raise typer.Exit(1)
    
    current_version = Version(get_current_fm_version())
    
    skip_backup_list = []
    if skip_backup_for:
        skip_backup_list = [b.strip() for b in skip_backup_for.split(",")]
    
    exclude_bench_list = []
    if exclude_bench:
        exclude_bench_list = [b.strip() for b in exclude_bench.split(",")]
    
    target_benches = None
    if benchname:
        bench_path = CLI_BENCHES_DIRECTORY / benchname
        if not bench_path.exists():
            richprint.error(f"Bench '{benchname}' does not exist")
            raise typer.Exit(1)
        target_benches = [benchname]
    elif all_benches:
        target_benches = []
        if CLI_BENCHES_DIRECTORY.exists():
            for bench_path in CLI_BENCHES_DIRECTORY.iterdir():
                if bench_path.is_dir() and (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists():
                    if bench_path.name not in exclude_bench_list:
                        target_benches.append(bench_path.name)
    
    if target_benches is not None and len(target_benches) > 0:
        migrations = MigrationExecutor(
            fm_config_manager,
            skip_backup=skip_backup,
            skip_backup_for=skip_backup_list,
            exclude_benches=exclude_bench_list,
            force=force,
            target_benches=target_benches,
        )
        
        migration_status = migrations.execute()
        
        if not migration_status:
            richprint.print(f"Rolled back to previous version of fm {migrations.prev_version}")
            raise typer.Exit(1)
        
        from frappe_manager.migration_manager.bench_migration_state import set_bench_migration_version
        for bench_name in target_benches:
            if bench_name in migrations.migrate_benches:
                bench_data = migrations.migrate_benches[bench_name]
                if bench_data['last_migration_version'] == current_version and not bench_data['exception']:
                    bench_path = CLI_BENCHES_DIRECTORY / bench_name
                    if bench_path.exists() and (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists():
                        set_bench_migration_version(bench_path, current_version)
    
    if system:
        fm_config_manager.set_system_migration_version(current_version)
        fm_config_manager.export_to_toml()
    
    richprint.print(
        f"Successfully migrated to v{get_current_fm_version()}",
        emoji_code=":white_check_mark:"
    )

