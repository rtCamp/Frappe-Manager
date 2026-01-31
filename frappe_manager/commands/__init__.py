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
from typing import Annotated, List, Optional, cast
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType, AppConfig, RestartPolicyEnum
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
from frappe_manager.migration_manager.migration_executor import (
    MigrationExecutor,
    needs_migration,
    needs_system_migration,
    get_benches_needing_migration,
)
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
from frappe_manager.commands.services import services_app
from frappe_manager.commands.self import self_app
from frappe_manager.sub_commands.ssl_command import ssl_root_command
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType, RestartPolicyEnum
from frappe_manager.migration_manager.version import Version
from frappe_manager.docker import ComposeFile
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler


# Helper functions


def check_bench_migration_required(bench_name: Optional[str]) -> None:
    from frappe_manager.migration_manager.bench_migration_state import bench_needs_migration

    if not bench_name:
        return

    bench_path = CLI_BENCHES_DIRECTORY / bench_name

    if not bench_path.exists():
        return

    current_version = Version(get_current_fm_version())

    if bench_needs_migration(bench_path, current_version):
        richprint.stop()

        bench_path = CLI_BENCHES_DIRECTORY / bench_name
        from frappe_manager.migration_manager.bench_migration_state import get_bench_migration_version

        bench_version = get_bench_migration_version(bench_path)
        fm_version = Version(get_current_fm_version())

        richprint.warning(f"Bench migration required: {bench_name} (v{bench_version} → v{fm_version})\n", emoji_code="")
        richprint.print("Bench migration updates configuration and applies necessary changes.\n", emoji_code="")
        richprint.print(f"Run: [cyan]fm migrate {bench_name}[/cyan]\n", emoji_code="")
        raise typer.Exit(0)


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


# Create main Typer app
app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

# Register subcommands
app.add_typer(services_app, name="services", help="Handle global services.")
app.add_typer(self_app, name="self", help="Perform operations related to the [bold][blue]fm[/bold][/blue] itself.")
app.add_typer(ssl_root_command, name="ssl", help="Perform operations related to ssl.")


# App callback (runs before all commands)
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
            allowed_without_system = ["migrate", "version", "self", "stop", "delete"]

            if needs_system_migration(fm_config_manager):
                if invoked_command not in allowed_without_system:
                    richprint.stop()

                    system_version = fm_config_manager.get_system_migration_version()
                    fm_version = Version(get_current_fm_version())
                    richprint.warning(f"System migration required: v{system_version} → v{fm_version}\n", emoji_code="")
                    richprint.print(
                        "System migration is required to update Docker images and global services.", emoji_code=""
                    )
                    richprint.print("Bench migrations are optional and can be done individually.\n", emoji_code="")
                    richprint.print("Please see help for fm migrate command: fm migrate --help")
                    raise typer.Exit(0)

            services_manager: ServicesManager = ServicesManager(
                verbose=ctx.obj["verbose"],
                invoked_subcommand=ctx.invoked_subcommand,
            )

            services_manager.init()

            # Don't start services for migrate command (migration handles its own service lifecycle)
            should_start_services = invoked_command != "migrate"

            try:
                services_manager.entrypoint_checks(start=should_start_services)
            except ServicesNotCreated as e:
                services_manager.remove_itself()
                richprint.exit(f"Not able to create services. {e}")

        ctx.obj["services"] = services_manager
        ctx.obj['fm_config_manager'] = fm_config_manager


# Import extracted read-only commands (Step 3)
from frappe_manager.commands.list import list as list_benches
from frappe_manager.commands.info import info
from frappe_manager.commands.logs import logs

# Import extracted lifecycle commands (Step 4)
from frappe_manager.commands.start import start
from frappe_manager.commands.stop import stop
from frappe_manager.commands.restart import restart
from frappe_manager.commands.shell import shell

# Import extracted complex commands (Step 5)
from frappe_manager.commands.create import create
from frappe_manager.commands.delete import delete
from frappe_manager.commands.update import update
from frappe_manager.commands.reset import reset

# Import extracted remaining commands (Step 6)
from frappe_manager.commands.code import code
from frappe_manager.commands.ngrok import ngrok
from frappe_manager.commands.migrate import migrate

# Register all commands with the app
app.command(name="create", no_args_is_help=True)(create)
app.command(name="delete")(delete)
app.command(name="list")(list_benches)
app.command(name="start")(start)
app.command(name="stop")(stop)
app.command(name="code")(code)
app.command(name="logs")(logs)
app.command(name="shell")(shell)
app.command(name="info")(info)
app.command(name="update", no_args_is_help=True)(update)
app.command(name="reset")(reset)
app.command(name="restart")(restart)
app.command(name="ngrok")(ngrok)
app.command(name="migrate")(migrate)

# Export app and helpers for backward compatibility
__all__ = ["app", "app_callback", "check_bench_migration_required", "get_output_handler"]
