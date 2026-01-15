from pathlib import Path
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.utils.site import pull_docker_images
import typer
import os
import sys
import shutil
from typing import Annotated, List, Optional
from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.ngrok import create_tunnel
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.modules.app_cloner import AppCloner
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_DIR,
    DEFAULT_EXTENSIONS,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
    SiteServicesEnum,
    CLI_BENCHES_DIRECTORY,
)
from frappe_manager.docker import DockerClient
from frappe_manager.logger import log
from frappe_manager.logger.context import LoggerContext
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    apps_list_validation_callback,
    create_command_sitename_callback,
    frappe_branch_validation_callback,
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
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.migration_manager.version import Version
from frappe_manager.docker import ComposeFile
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
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

    # Create base handler with verbose setting
    rich = RichOutputHandler(verbose=verbose)

    # Get base logger
    base_logger = log.get_logger()

    # Wrap with context (empty context if not provided)
    contextual_logger = ContextualLogger(base_logger, context)

    # Wrap with logging for automatic file logging
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
    Frappe-Manager for creating frappe development environments.

    Verbosity options:
        -v: Info level (show detailed messages)
        -vv: Debug level (show all messages including debug)
        --log-level: Explicit log level (overrides -v)

    Examples:
        fm create test              # WARNING level (default)
        fm create test -v           # INFO level
        fm create test -vv          # DEBUG level
        fm create test --log-level debug  # DEBUG level (explicit)
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
        first_time_install = False

        richprint.start("Working")

        if not CLI_DIR.exists():
            # creating the sites dir
            # TODO check if it's writeable and readable -> by writing a file to it and catching exception
            CLI_DIR.mkdir(parents=True, exist_ok=True)
            CLI_BENCHES_DIRECTORY.mkdir(parents=True, exist_ok=True)
            richprint.print(f"fm directory doesn't exists! Created at -> {str(CLI_DIR)}")
            first_time_install = True
        else:
            if not CLI_DIR.is_dir():
                richprint.exit("Sites directory is not a directory! Aborting!")

        # logging
        global logger
        console_level = level_name if ctx.obj["verbose"] else None
        logger = log.get_logger(console_level=console_level)

        # Configure Python logger level based on CLI flags
        import logging

        logger.setLevel(getattr(logging, level_name))

        logger.info("")
        logger.info(f"{':' * 20}FM Invoked{':' * 20}")
        logger.info("")

        # logging command provided by user
        logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
        logger.info(f"LOG LEVEL: {level_name}")
        logger.info("-" * 20)

        # check docker daemon service
        if not DockerClient().server_running():
            richprint.exit("Docker daemon not running. Please start docker service.")

        fm_config_manager: FMConfigManager = FMConfigManager.import_from_toml()

        # docker pull
        if first_time_install:
            if not fm_config_manager.root_path.exists():
                richprint.print("It seems like the first installation. Pulling docker images...️", "🔍")

                completed_status = pull_docker_images()

                if not completed_status:
                    shutil.rmtree(CLI_DIR)
                    richprint.exit("Aborting. Not able to pull all required Docker images.")

                current_version = Version(get_current_fm_version())
                fm_config_manager.version = current_version
                fm_config_manager.export_to_toml()

        migrations = MigrationExecutor(fm_config_manager)
        migration_status = migrations.execute()

        if not migration_status:
            richprint.print(f"Rolled back to previous version of fm {migrations.prev_version}.")
            raise typer.Exit(0)  # Exit gracefully since rollback is intentional

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
    benchname: Annotated[str, typer.Argument(help="Name of the bench", callback=create_command_sitename_callback)],
    apps: Annotated[
        List[str],
        typer.Option(
            "--apps",
            "-a",
            help="FrappeVerse apps to install. App should be specified in format <appname>:<branch> or <appname>.",
            callback=apps_list_validation_callback,
            show_default=False,
        ),
    ] = [],
    environment: Annotated[
        FMBenchEnvType, typer.Option("--environment", "-e", help="Select bench environment type.")
    ] = FMBenchEnvType.dev,
    developer_mode: Annotated[
        EnableDisableOptionsEnum, typer.Option(help="Toggle frappe developer mode.")
    ] = EnableDisableOptionsEnum.disable,
    frappe_branch: Annotated[
        str, typer.Option(help="Specify the branch name for frappe app", callback=frappe_branch_validation_callback)
    ] = "version-15",
    template: Annotated[bool, typer.Option(help="Create template bench.")] = False,
    admin_pass: Annotated[
        str,
        typer.Option(help="Password for the 'Administrator' User."),
    ] = "admin",
    alias_domains: Annotated[
        Optional[str],
        typer.Option(
            help="Comma-separated list of alias domains for the site (e.g., 'www.example.com,api.example.com'). These domains will be configured as network aliases for accessing the site. Use 'fm ssl add' to configure SSL certificates for domains.",
            callback=alias_domains_validation_callback,
            show_default=False,
        ),
    ] = None,
    github_token: Annotated[
        Optional[str],
        typer.Option(
            "--github-token",
            "-t",
            help="GitHub personal access token for private repositories. Can also be set via GITHUB_TOKEN environment variable.",
            envvar="GITHUB_TOKEN",
            show_default=False,
        ),
    ] = None,
):
    """
    Create a new bench.

    Examples:

        # Create bench with public apps
        fm create mybench.localhost --apps erpnext:version-15 --apps hrms:version-15

        # Create bench with private apps (using environment variable)
        export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
        fm create mybench.localhost --apps mycompany/private-app:main

        # Create bench with private apps (using CLI option)
        fm create mybench.localhost --apps mycompany/private-app:main --github-token ghp_xxxxxxxxxxxxx

        # Create bench with subdirectory app (monorepo)
        fm create mybench.localhost --apps frappe/frappe:version-15#apps/frappe
    """

    services_manager: ServicesManager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create context for this operation
    context = LoggerContext(bench=benchname, operation="create")
    output = get_output_handler(ctx, context=context)
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    bench_path = bench_service.benches_directory / benchname
    bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME

    if developer_mode == EnableDisableOptionsEnum.enable:
        developer_mode_status = True
    elif developer_mode == EnableDisableOptionsEnum.disable:
        developer_mode_status = False

    bench_config: BenchConfig = BenchConfig(
        name=benchname,
        apps_list=apps,
        frappe_branch=frappe_branch,
        developer_mode=True if environment == FMBenchEnvType.dev else developer_mode_status,
        admin_tools=True if environment == FMBenchEnvType.dev else False,
        admin_pass=admin_pass,
        # TODO get this info from services, maybe ?
        environment_type=environment,
        root_path=bench_config_path,
        ssl_certificates=[],  # No SSL certificates by default, use 'fm ssl add' to add them
        alias_domains=alias_domains if alias_domains else [],
        github_token=github_token,  # NEW: GitHub token for private repos
        use_uv=True,  # NEW: Always use UV with automatic fallback
    )

    # Validate repositories exist BEFORE creating any infrastructure
    # This prevents failed bench creation due to invalid repos
    if apps:
        output.info("Validating app repositories...")
        apps_config = bench_config.get_apps_config()
        valid, errors = AppCloner.validate_repos_exist(apps_config, github_token)

        if not valid:
            output.display_error("Repository validation failed:")
            for error in errors:
                output.display_error(f"  {error}")
            output.display_error("\nPlease check the repository names, branches, and authentication.")
            output.display_error("For private repos, use --github-token or set GITHUB_TOKEN environment variable.")
            raise typer.Exit(1)

        output.print(f"✓ Validated {len(apps_config)} app repositories")

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
    force: Annotated[bool, typer.Option("--force", "-f", help="Force delete bench.")] = False,
    delete_db_from_global_db: Annotated[
        Optional[bool],
        typer.Option(
            "--delete-db-from-global-db/--no-delete-db-from-global-db",
            help="Delete database from global-db. If not specified, prompts interactively when DB is in global-db.",
        ),
    ] = None,
):
    """Delete a bench."""

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
    """Lists all of the available benches."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create context for this operation
    context = LoggerContext(operation="list")
    output = get_output_handler(ctx, context=context)
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager, verbose=verbose, output_handler=output)
    table = bench_service.list_benches_table()
    if table.row_count:
        richprint.stdout.print(table)


@app.command()
def start(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force recreate bench containers")] = False,
    sync_bench_config_changes: Annotated[
        bool, typer.Option("--sync-config", help="Sync bench configuration changes")
    ] = False,
    reconfigure_supervisor: Annotated[
        bool, typer.Option("--reconfigure-supervisor", help="Reconfigure supervisord configuration")
    ] = False,
    reconfigure_common_site_config: Annotated[
        bool, typer.Option("--reconfigure-common-site-config", help="Reconfigure common_site_config.json")
    ] = False,
    reconfigure_workers: Annotated[
        bool, typer.Option("--reconfigure-workers", help="Reconfigure workers configuration")
    ] = False,
    include_default_workers: Annotated[bool, typer.Option(help="Include default worker configuration")] = True,
    include_custom_workers: Annotated[bool, typer.Option(help="Include custom worker configuration")] = True,
    sync_dev_packages: Annotated[bool, typer.Option("--sync-dev-packages", help="Sync dev packages")] = False,
):
    """Start a bench."""

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
    user: Annotated[str, typer.Option(help="Connect as this user.")] = "frappe",
    extensions: Annotated[
        List[str],
        typer.Option(
            "--extension",
            "-e",
            help="List of extensions to install in vscode at startup.Provide extension id eg: ms-python.python",
            callback=code_command_extensions_callback,
            show_default=False,
        ),
    ] = DEFAULT_EXTENSIONS,
    force_start: Annotated[
        bool, typer.Option("--force-start", "-f", help="Force start the site before attaching to container.")
    ] = False,
    debugger: Annotated[bool, typer.Option("--debugger", "-d", help="Sync vscode debugger configuration.")] = False,
    workdir: Annotated[
        str, typer.Option("--work-dir", "-w", help="Set working directory in vscode.")
    ] = '/workspace/frappe-bench',
):
    """Open bench in vscode."""

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
    service: Annotated[
        Optional[SiteServicesEnum], typer.Option(help="Specify compose service name to show container logs.")
    ] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs.")] = False,
):
    """Show frappe server logs or container logs for a given bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="logs")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
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
    command: Annotated[
        Optional[str], typer.Option("-c", "--command", help="Execute a single command and exit.")
    ] = None,
    user: Annotated[Optional[str], typer.Option(help="Connect as this user.", show_default=False)] = None,
    service: Annotated[
        SiteServicesEnum, typer.Option(help="Specify compose service name for which to spawn shell.")
    ] = SiteServicesEnum.frappe,
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

    Exit code from the executed command is preserved for scripting.
    """

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="shell")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

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

        exit_code = bench.execute_command(SiteServicesEnum(service).value, exec_command, user)

        # Exit with the command's exit code
        if exit_code != 0:
            raise typer.Exit(exit_code)
    else:
        # Interactive shell mode (original behavior)
        bench.shell(SiteServicesEnum(service).value, user)


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
    """Shows information about given bench."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="info")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
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
):
    """Update bench."""

    services_manager = ctx.obj["services"]

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="update")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    bench_config_save = False

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    if developer_mode:
        if developer_mode == EnableDisableOptionsEnum.enable:
            bench.bench_config.developer_mode = True
            richprint.print("Enabling frappe developer mode.")
            bench.set_common_bench_config({'developer_mode': bench.bench_config.developer_mode})
            richprint.print("Enabled frappe developer mode.")
        elif developer_mode == EnableDisableOptionsEnum.disable:
            bench.bench_config.developer_mode = False
            richprint.print("Disabling frappe developer mode.")
            bench.set_common_bench_config({'developer_mode': bench.bench_config.developer_mode})
            richprint.print("Enabled frappe developer mode.")

        bench_config_save = True

    if environment:
        richprint.change_head(f"Switching bench environemnt to {environment.value}")
        bench.bench_config.environment_type = environment
        bench.switch_bench_env()
        richprint.print(f"Switched bench environemnt to {environment.value}.")
        bench_config_save = True

    if admin_tools:
        if admin_tools == EnableDisableOptionsEnum.enable:
            richprint.change_head("Enabling Admin-tools")
            bench.bench_config.admin_tools = True

            if not bench.admin_tools.compose_file_manager.compose_path.exists():
                bench.sync_admin_tools_compose()
            else:
                bench.admin_tools.enable(force_configure=mailpit_as_default_mail_server)

            bench_config_save = True
            richprint.print("Enabled Admin-tools.")

        elif admin_tools == EnableDisableOptionsEnum.disable:
            if not bench.admin_tools.compose_file_manager.compose_path.exists() or not bench.bench_config.admin_tools:
                richprint.print("Admin tools is already disabled.")
                return
            else:
                bench.bench_config.admin_tools = False
                bench.admin_tools.disable()
                bench_config_save = True

    # Handle alias domain updates
    if add_alias or remove_alias:
        add_domains_list = add_alias if add_alias else []
        remove_domains_list = remove_alias if remove_alias else []

        richprint.change_head("Updating alias domains")
        bench.update_alias_domains(add_domains=add_domains_list, remove_domains=remove_domains_list)
        richprint.print("Alias domains updated successfully.")

    # Handle upload limit update
    if upload_limit:
        richprint.change_head(f"Updating upload size limit to {upload_limit}")
        bench.update_upload_limit(upload_limit)
        # Note: bench_config already saved by update_upload_limit(), no need to set bench_config_save

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
    """Reset bench site and reinstall all installed apps."""

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
):
    """Restart bench services."""

    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="restart")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if web:
        bench.restart_web_containers_services()

    if workers:
        bench.restart_workers_containers_services()

    if redis:
        bench.restart_redis_services_containers()


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
    """Create ngrok tunnel for the bench."""
    services_manager = ctx.obj["services"]
    verbose = ctx.obj['verbose']

    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="ngrok")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)

    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    fm_config_manager: FMConfigManager = ctx.obj["fm_config_manager"]

    richprint.start("Setting up ngrok tunnel")

    # Use token from config if available and no token provided
    if not auth_token and fm_config_manager.ngrok_auth_token:
        auth_token = fm_config_manager.ngrok_auth_token
        richprint.print("Using ngrok auth token from config file", emoji_code=":key:")
    elif not auth_token:
        richprint.exit(
            "Ngrok auth token is required. Please provide it with --auth-token or set NGROK_AUTHTOKEN environment variable."
        )

    # If token provided and not in config, ask to save
    if auth_token and not fm_config_manager.ngrok_auth_token:
        richprint.print("New auth token provided", emoji_code=":new:")
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
