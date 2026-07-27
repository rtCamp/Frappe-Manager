import os
import secrets
import shutil
import sys
import uuid
from pathlib import Path
from typing import Annotated, List, Optional, cast

import typer
from typer_examples import install

from frappe_manager import (
    CLI_BENCH_CONFIG_FILE_NAME,
    CLI_BENCHES_DIRECTORY,
    CLI_DIR,
    CLI_FM_CONFIG_PATH,
    DEFAULT_EXTENSIONS,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
    SiteServicesEnum,
)
from frappe_manager.commands.self import self_app
from frappe_manager.commands.services import services_app
from frappe_manager.commands.ssl import ssl_app
from frappe_manager.docker import ComposeFile, DockerClient
from frappe_manager.logger import log, set_context
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.bench_migration_state import (
    bench_needs_migration,
    get_bench_migration_version,
    set_bench_migration_version,
)
from frappe_manager.migration_manager.migration_executor import (
    MigrationExecutor,
    get_benches_needing_migration,
    needs_fm_infrastructure_migration,
    needs_migration,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.ngrok import create_tunnel
from frappe_manager.output_manager import OutputHandler, get_global_output_handler, spinner, temporary_stop
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.services_manager.services_exceptions import ServicesNotCreated
from frappe_manager.site_manager.bench_config import AppConfig, BenchConfig, FMBenchEnvType, RestartPolicyEnum
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.domain_conflict import DomainConflictError, validate_domains_unique
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules.app_cloner import AppCloner
from frappe_manager.site_manager.site import Bench
from frappe_manager.utils.callbacks import (
    alias_domains_validation_callback,
    apps_list_validation_callback,
    code_command_extensions_callback,
    create_command_sitename_callback,
    sitename_callback,
    sites_autocompletion_callback,
    version_callback,
)
from frappe_manager.utils.helpers import (
    get_current_fm_version,
    is_cli_help_called,
)
from frappe_manager.utils.site import pull_docker_images, validate_sitename

# Helper functions


def get_bench_arg_from_context(ctx: typer.Context) -> str | None:
    """
    Extract bench/site name from command context.
    Commands use different parameter names (benchname, sitename, bench_name).
    """
    return ctx.params.get("benchname") or ctx.params.get("sitename") or ctx.params.get("bench_name")


def check_bench_migration_required(bench_name: str | None) -> None:
    from frappe_manager.migration_manager.bench_migration_state import bench_needs_migration

    if not bench_name:
        return

    bench_path = CLI_BENCHES_DIRECTORY / bench_name

    if not bench_path.exists():
        return

    current_version = Version(get_current_fm_version())

    if bench_needs_migration(bench_path, current_version):
        output = get_global_output_handler()
        output.stop()

        bench_path = CLI_BENCHES_DIRECTORY / bench_name
        from frappe_manager.migration_manager.bench_migration_state import get_bench_migration_version

        bench_version = get_bench_migration_version(bench_path)
        fm_version = Version(get_current_fm_version())

        output.warning(f"Bench migration required: {bench_name} (v{bench_version} → v{fm_version})\n", emoji_code="")
        output.print("Bench migration updates configuration and applies necessary changes.\n", emoji_code="")
        output.print(f"Run: [fm.info]fm migrate {bench_name}[/fm.info]\n", emoji_code="")
        raise typer.Exit(0)


# Create main Typer app
app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
# Activate typer-examples for the main Typer app
install(app)

# Register subcommands
app.add_typer(services_app, name="services", help="Handle global services.")
app.add_typer(self_app, name="self", help="Perform operations related to the [bold][blue]fm[/bold][/blue] itself.")
app.add_typer(ssl_app, name="ssl", help="Perform operations related to ssl.")


# App callback (runs before all commands)
@app.callback()
def app_callback(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output (info level)")] = False,
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", help="Set log level explicitly (debug|info|warning|error)"),
    ] = None,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            "-n",
            help="Run without interactive prompts. All prompts will error with suggestions for required flags.",
        ),
    ] = False,
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", help="Show Version.", callback=version_callback),
    ] = None,
):
    """
    Docker Compose based CLI for managing Frappe benches.

    Create, manage, and develop isolated Frappe environments using containers.
    Each bench runs independently with its own apps, database, and configuration.
    """
    ctx.obj = {}

    # Ambient logging context: every record this invocation emits -- from any
    # module, thread (via ctx_submit), or the output mirror -- carries these.
    set_context(correlation_id=str(uuid.uuid4()), operation=ctx.invoked_subcommand)

    # Import early for validation error reporting
    from frappe_manager.output_manager import get_global_output_handler, set_global_output_handler

    # Determine effective log level
    if log_level:
        # Explicit --log-level takes precedence
        level_name = log_level.upper()

        # Validate log level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        if level_name not in valid_levels:
            output = get_global_output_handler()
            output.display_error(f"Invalid log level: {log_level}. Must be one of: {', '.join(valid_levels).lower()}")
            raise typer.Exit(1)
    elif verbose:
        # -v flag sets INFO level
        level_name = "INFO"
    else:
        # Default: WARNING
        level_name = "WARNING"

    # Store in context for commands
    ctx.obj["log_level"] = level_name
    ctx.obj["verbose"] = verbose or level_name in ["INFO", "DEBUG"]
    ctx.obj["non_interactive"] = non_interactive

    # Upgrade global output handler to LoggingOutputHandler now that we have CLI args
    basic_handler = get_global_output_handler()
    upgraded_handler = LoggingOutputHandler(basic_handler)
    set_global_output_handler(upgraded_handler)

    output = get_global_output_handler()
    output.set_interactive_mode(non_interactive_flag=non_interactive)

    help_called = is_cli_help_called(ctx)
    ctx.obj["is_help_called"] = help_called

    if not help_called:
        output = get_global_output_handler()
        with spinner(output, "Working"):
            if not CLI_DIR.exists():
                CLI_DIR.mkdir(parents=True, exist_ok=True)
                CLI_BENCHES_DIRECTORY.mkdir(parents=True, exist_ok=True)
                output.print(f"fm directory doesn't exists! Created at -> {CLI_DIR!s}")
            elif not CLI_DIR.is_dir():
                output.exit("Sites directory is not a directory! Aborting!")

            global logger
            console_level = level_name if ctx.obj["verbose"] else None

            fm_config_manager: FMConfigManager = FMConfigManager.import_from_toml()
            file_level = fm_config_manager.logs.file_level

            # Theme (colors) + style (layout) from config; env FM_THEME/FM_STYLE win.
            from frappe_manager.output_manager.style import set_output_style
            from frappe_manager.output_manager.theme import apply_output_theme

            try:
                apply_output_theme(fm_config_manager.output.theme, fm_config_manager.output.colors)
                set_output_style(fm_config_manager.output.style)
            except Exception as e:  # cosmetic subsystem: warn + defaults, never block commands
                output.warning(f"Output theme/style config: {e} -- using defaults.")

            logger = log.get_logger(console_level=console_level, file_level=file_level)

            logger.info("")
            logger.info(f"{':' * 20}FM Invoked{':' * 20}")
            logger.info("")

            logger.info(f"RUNNING COMMAND: {' '.join(sys.argv[1:])}")
            logger.info(f"LOG LEVEL: {level_name}")
            logger.info("-" * 20)

            if not DockerClient().server_running():
                output.exit("Docker daemon not running. Please start docker service")

            if not CLI_FM_CONFIG_PATH.exists():
                output.print("First installation detected. Pulling docker images...️", "🔍")

                completed_status = pull_docker_images()

                if not completed_status:
                    if CLI_DIR.exists():
                        shutil.rmtree(CLI_DIR)
                    output.exit("Aborting. Not able to pull all required Docker images")

                current_version = Version(get_current_fm_version())
                fm_config_manager.version = current_version
                fm_config_manager.export_to_toml()

            invoked_command = ctx.invoked_subcommand or "no-command"

            from frappe_manager.migration_manager.migration_constants import (
                MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS,
                MIGRATION_CHECK_WHITELIST_COMMANDS,
            )

            def get_full_command_path() -> str:
                """
                Build full command path from sys.argv for multi-level commands.

                Multi-level commands (self, ssl, services) have subcommands and return paths like "ssl add".
                Single-level commands take arguments (start, stop, create) and return just the base command.
                Stops parsing at flags (--) or path-like arguments (/ or ~). Limits depth to 2 levels max.
                """
                # Commands that have subcommands (multi-level structure)
                MULTI_LEVEL_COMMANDS = {"self", "ssl", "services"}

                if len(sys.argv) < 2:
                    return invoked_command

                first_command = sys.argv[1] if len(sys.argv) > 1 else invoked_command

                # If it's not a multi-level command, return just the first command
                if first_command not in MULTI_LEVEL_COMMANDS:
                    return first_command if not first_command.startswith("-") else invoked_command

                # For multi-level commands, build the full path (max 2 levels)
                command_parts = []
                for arg in sys.argv[1:]:
                    # Stop at flags
                    if arg.startswith("-"):
                        break
                    # Stop at paths (likely bench names like /path or ~/path)
                    if arg.startswith("/") or arg.startswith("~"):
                        break
                    # Limit to max 2 command levels (e.g., "self compose")
                    if len(command_parts) >= 2:
                        break

                    command_parts.append(arg)

                return " ".join(command_parts) if command_parts else invoked_command

            full_command = get_full_command_path()

            commands_skip_migration_check = MIGRATION_CHECK_WHITELIST_COMMANDS

            commands_skip_bench_migration = ["stop", "delete"] + MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS

            # Get bench argument if present
            bench_arg = get_bench_arg_from_context(ctx)
            bench_path = CLI_BENCHES_DIRECTORY / bench_arg if bench_arg else None

            # Check migration states
            fm_infrastructure_version = fm_config_manager.get_system_migration_version()
            current_version = Version(get_current_fm_version())
            infra_needs_migration = fm_infrastructure_version < current_version

            bench_needs_migration_flag = False
            bench_version = None
            if bench_path and bench_path.exists() and invoked_command not in commands_skip_bench_migration:
                bench_needs_migration_flag = bench_needs_migration(bench_path, current_version)
                if bench_needs_migration_flag:
                    bench_version = get_bench_migration_version(bench_path)

            should_check_migration = (
                invoked_command not in commands_skip_migration_check
                and full_command not in commands_skip_migration_check
            )

            if should_check_migration:
                output = get_global_output_handler()

                # Scenario 1: Infra needs migration
                if infra_needs_migration:
                    output.warning(
                        f"FM infrastructure needs update: v{fm_infrastructure_version} -> v{current_version}",
                    )
                    output.print("This updates CLI config and global services", emoji_code="  ")
                    output.print("", emoji_code="")

                    infra_choice = output.prompt_ask(
                        prompt="How would you like to proceed?",
                        choices=[
                            {"name": "Update now (recommended)", "value": "update"},
                            {"name": "Update later (run 'fm migrate' when ready)", "value": "skip"},
                        ],
                        default="update",
                        required_flag="'fm migrate' (run migration explicitly)",
                    )

                    if infra_choice == "update":
                        output.print("\n🔄 Updating FM infrastructure...\n", emoji_code="")

                        migrations = MigrationExecutor(
                            fm_config_manager,
                            migrate_fm_infrastructure=True,
                            auto_proceed=True,
                            on_failure="rollback",
                            output_handler=output,
                        )

                        with temporary_stop(output):
                            migration_status = migrations.execute()

                        if not migration_status:
                            output.display_error("FM infrastructure update failed")
                            output.print("Please run 'fm migrate' manually to fix.", emoji_code="")
                            raise typer.Exit(1)

                        fm_config_manager.set_system_migration_version(current_version)
                        fm_config_manager.export_to_toml()

                        output.print(f"FM infrastructure updated to v{current_version}\n", emoji_code="✅ ")

                        # Now check bench migration if bench arg present
                        if bench_needs_migration_flag and bench_arg and bench_version:
                            output.warning(
                                f"Bench '{bench_arg}' needs migration: v{bench_version} -> v{current_version}",
                            )
                            output.print("This may modify bench configuration and services.", emoji_code="")
                            output.print("", emoji_code="")

                            bench_choice = output.prompt_ask(
                                prompt=f"Migrate bench '{bench_arg}' now?",
                                choices=[
                                    {"name": "Update now", "value": "update"},
                                    {
                                        "name": f"Update later (run 'fm migrate {bench_arg}' when ready)",
                                        "value": "skip",
                                    },
                                ],
                                default="update",
                                required_flag=f"'fm migrate {bench_arg}' (run migration explicitly)",
                            )

                            if bench_choice == "update":
                                output.print(f"\nMigrating bench '{bench_arg}'...\n", emoji_code="🔄 ")

                                bench_migrations = MigrationExecutor(
                                    fm_config_manager,
                                    target_benches=[bench_arg],
                                    auto_proceed=True,
                                    on_failure="rollback",
                                    output_handler=output,
                                )

                                with temporary_stop(output):
                                    bench_status = bench_migrations.execute()

                                if not bench_status:
                                    output.display_error(f"Bench migration failed for '{bench_arg}'")
                                    output.print(f"Please run 'fm migrate {bench_arg}' manually.", emoji_code="")
                                    raise typer.Exit(1)

                                set_bench_migration_version(bench_path, current_version)  # type: ignore[arg-type]
                                output.print(f"Bench '{bench_arg}' migrated to v{current_version}\n", emoji_code="✅ ")
                            else:
                                output.print("", emoji_code="")
                                output.warning(f"Skipped bench migration. Run 'fm migrate {bench_arg}' when ready.")
                                output.print("Note: Bench may not work correctly until migrated.", emoji_code="")
                                output.print("", emoji_code="")
                                output.display_error(f"Cannot {invoked_command} '{bench_arg}' - migration required")
                                output.print(f"Run 'fm migrate {bench_arg}' first", emoji_code="")
                                raise typer.Exit(1)

                    else:
                        output.display_error("Cannot proceed - FM infrastructure migration required")
                        output.print("Run 'fm migrate' when ready", emoji_code="")
                        raise typer.Exit(1)

                # Scenario 2: Only bench needs migration (infra already up-to-date)
                elif bench_needs_migration_flag and bench_arg and bench_version:
                    output.warning(f"Bench '{bench_arg}' needs migration: v{bench_version} -> v{current_version}")
                    output.print("This may modify bench configuration and services.", emoji_code="")
                    output.print("", emoji_code="")

                    bench_choice = output.prompt_ask(
                        prompt=f"Migrate bench '{bench_arg}' now?",
                        choices=[
                            {"name": "Update now", "value": "update"},
                            {"name": f"Update later (run 'fm migrate {bench_arg}' when ready)", "value": "skip"},
                        ],
                        default="update",
                        required_flag=f"'fm migrate {bench_arg}' (run migration explicitly)",
                    )

                    if bench_choice == "update":
                        output.print(f"\nMigrating bench '{bench_arg}'...\n", emoji_code="🔄 ")

                        bench_migrations = MigrationExecutor(
                            fm_config_manager,
                            target_benches=[bench_arg],
                            auto_proceed=True,
                            on_failure="rollback",
                            output_handler=output,
                        )

                        with temporary_stop(output):
                            bench_status = bench_migrations.execute()

                        if not bench_status:
                            output.display_error(f"Bench migration failed for '{bench_arg}'")
                            output.print(f"Please run 'fm migrate {bench_arg}' manually.", emoji_code="")
                            raise typer.Exit(1)

                        set_bench_migration_version(bench_path, current_version)  # type: ignore[arg-type]
                        output.print(f"Bench '{bench_arg}' migrated to v{current_version}\n", emoji_code="✅ ")
                    else:
                        output.print("", emoji_code="")
                        output.warning(f"Skipped bench migration. Run 'fm migrate {bench_arg}' when ready.")
                        output.print("Note: Bench may not work correctly until migrated.", emoji_code="")
                        output.print("", emoji_code="")
                        output.display_error(f"Cannot {invoked_command} '{bench_arg}' - migration required")
                        output.print(f"Run 'fm migrate {bench_arg}' first", emoji_code="")
                        raise typer.Exit(1)

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
                output.exit(f"Not able to create services. {e}")

            ctx.obj["services"] = services_manager
            ctx.obj["fm_config_manager"] = fm_config_manager


# Import extracted read-only commands (Step 3)
# Import extracted remaining commands (Step 6)
from frappe_manager.commands.bake import bake
from frappe_manager.commands.code import code

# Import extracted complex commands (Step 5)
from frappe_manager.commands.create import create
from frappe_manager.commands.delete import delete
from frappe_manager.commands.deploy import deploy, switch
from frappe_manager.commands.info import info
from frappe_manager.commands.list import list as list_benches
from frappe_manager.commands.logs import logs
from frappe_manager.commands.migrate import migrate
from frappe_manager.commands.ngrok import ngrok
from frappe_manager.commands.reset import reset
from frappe_manager.commands.restart import restart
from frappe_manager.commands.shell import shell

# Import extracted lifecycle commands (Step 4)
from frappe_manager.commands.start import start
from frappe_manager.commands.stop import stop
from frappe_manager.commands.update import update

# Register all commands with the app
app.command(name="create", no_args_is_help=True)(create)
app.command(name="delete")(delete)
app.command(name="list")(list_benches)
app.command(name="start")(start)
app.command(name="stop")(stop)
app.command(name="code")(code)
app.command(name="logs")(logs)
app.command(name="shell", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(shell)
app.command(name="info")(info)
app.command(name="update", no_args_is_help=True)(update)
app.command(name="reset")(reset)
app.command(name="restart")(restart)
app.command(name="ngrok")(ngrok)
app.command(name="migrate")(migrate)
app.command(name="bake", no_args_is_help=True)(bake)
app.command(name="deploy", no_args_is_help=True)(deploy)
app.command(name="switch", no_args_is_help=True)(switch)

# Export app and helpers for backward compatibility
__all__ = ["app", "app_callback", "check_bench_migration_required", "get_bench_arg_from_context"]
