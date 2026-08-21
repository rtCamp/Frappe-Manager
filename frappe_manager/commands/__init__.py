import os
import secrets
import shutil
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, List, Optional, cast

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

    Only useful on a *subcommand* context. On the group context built for ``app_callback``
    Click populates ``ctx.params`` with the group's own options only, so this returns None
    there and the caller falls back to :func:`get_bench_arg_from_argv`.
    """
    return ctx.params.get("benchname") or ctx.params.get("sitename") or ctx.params.get("bench_name")


def get_bench_arg_from_argv(command_path: str) -> str | None:
    """
    Extract the bench/site name from ``sys.argv``.

    ``app_callback`` is a group callback: Click clears ``ctx.args`` and resolves the subcommand
    *after* the callback returns, so the subcommand's ``benchname`` argument is not reachable
    from the context. ``sys.argv`` is the only place it exists at that point -- the same source
    ``get_full_command_path()`` already parses.

    ``command_path`` is that resolved path ("start", "ssl add"): its tokens are consumed first
    (skipping any global flags typed before them), and the next token is the bench argument
    unless it is a flag or a filesystem path. Anything more clever would need the subcommand's
    own parser, which does not exist yet here; a missed name only means the callback gate stays
    quiet, because every bench command still re-checks via ``check_bench_migration_required``.
    """
    pending = command_path.split()

    for token in sys.argv[1:]:
        if pending:
            if token == pending[0]:
                pending.pop(0)
            continue
        if token.startswith(("-", "/", "~")):
            break
        return token

    return None


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
        # Exit 1, not 0: this refuses the command without doing anything, so `fm start x && ...`
        # and any CI step or systemd unit checking $? must see a failure.
        raise typer.Exit(1)


def _prompt_and_run_migration(
    output: OutputHandler,
    fm_config_manager: FMConfigManager,
    *,
    warning: str,
    detail: str,
    detail_emoji: str,
    prompt: str,
    choices: list[dict[str, str]],
    required_flag: str,
    start_notice: str,
    start_emoji: str,
    executor_kwargs: dict[str, Any],
    failure_error: str,
    failure_hint: str,
    record_version: Callable[[], None],
    success_notice: str,
    skip_error: str,
    skip_hint: str,
    skip_warning: str | None = None,
    skip_note: str | None = None,
) -> None:
    """
    Warn about a pending migration, ask the user, then either run it or refuse the command.

    Serves both migration gates in ``app_callback``: the fm infrastructure gate and the bench
    gate (which runs either nested after a successful infrastructure update, or standalone when
    the infrastructure is already current).

    The order of side effects is part of the contract: warn -> detail -> prompt_ask -> (on
    "update") start notice -> build ``MigrationExecutor`` -> ``execute()`` *inside*
    ``temporary_stop(output)`` -> record the new version *only* after a successful execute ->
    success notice. A falsy execute result, or any answer other than "update", refuses the
    command with ``typer.Exit(1)``.

    Every parameter exists because the call sites genuinely differ; do not re-inline this:
    the wording and emoji placement (infra puts its emoji in the message and indents the
    detail line, bench passes the emoji separately), the executor arguments
    (``migrate_fm_infrastructure`` vs ``target_benches``), where the new version is recorded
    (``fm_config_manager.set_system_migration_version`` + ``export_to_toml`` vs
    ``set_bench_migration_version(bench_path, ...)`` -- hence the ``record_version`` callback),
    and the refusal text: the bench gate emits an extra "skipped"/"may not work" notice
    (``skip_warning`` / ``skip_note``) that the infrastructure gate does not.
    """
    output.warning(warning)
    output.print(detail, emoji_code=detail_emoji)
    output.print("", emoji_code="")

    choice = output.prompt_ask(
        prompt=prompt,
        choices=choices,
        default="update",
        required_flag=required_flag,
    )

    if choice == "update":
        output.print(start_notice, emoji_code=start_emoji)

        migrations = MigrationExecutor(
            fm_config_manager,
            **executor_kwargs,
            output_handler=output,
        )

        with temporary_stop(output):
            migration_status = migrations.execute()

        if not migration_status:
            output.display_error(failure_error)
            output.print(failure_hint, emoji_code="")
            raise typer.Exit(1)

        record_version()
        output.print(success_notice, emoji_code="✅ ")
        return

    if skip_warning is not None:
        output.print("", emoji_code="")
        output.warning(skip_warning)

    if skip_note is not None:
        output.print(skip_note, emoji_code="")
        output.print("", emoji_code="")

    output.display_error(skip_error)
    output.print(skip_hint, emoji_code="")
    raise typer.Exit(1)


# Create main Typer app
app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
# Activate typer-examples for the main Typer app
install(app)

# Register subcommands
app.add_typer(services_app, name="services", help="Handle global services.")
app.add_typer(self_app, name="self", help="Perform operations related to [bold][blue]fm[/bold][/blue] itself.")
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

    Create, manage, and develop isolated Frappe environments using containers. Each bench runs independently with its own apps, database, and configuration.
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

            # Get bench argument if present. The group context never carries the subcommand's
            # benchname, so sys.argv is what actually resolves it here.
            bench_arg = get_bench_arg_from_context(ctx) or get_bench_arg_from_argv(full_command)
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

                # Scenario 1: Infra needs migration. The gate either updates cleanly and falls
                # through, or raises typer.Exit(1) -- so the bench gate below still runs only
                # after a successful infra update, exactly as when it was nested inside it.
                if infra_needs_migration:

                    def record_infra_version() -> None:
                        fm_config_manager.set_system_migration_version(current_version)
                        fm_config_manager.export_to_toml()

                    _prompt_and_run_migration(
                        output,
                        fm_config_manager,
                        warning=f"FM infrastructure needs update: v{fm_infrastructure_version} -> v{current_version}",
                        detail="This updates CLI config and global services",
                        detail_emoji="  ",
                        prompt="How would you like to proceed?",
                        choices=[
                            {"name": "Update now (recommended)", "value": "update"},
                            {"name": "Update later (run 'fm migrate' when ready)", "value": "skip"},
                        ],
                        required_flag="'fm migrate' (run migration explicitly)",
                        start_notice="\n🔄 Updating FM infrastructure...\n",
                        start_emoji="",
                        executor_kwargs={
                            "migrate_fm_infrastructure": True,
                            "auto_proceed": True,
                            "on_failure": "rollback",
                        },
                        failure_error="FM infrastructure update failed",
                        failure_hint="Please run 'fm migrate' manually to fix.",
                        record_version=record_infra_version,
                        success_notice=f"FM infrastructure updated to v{current_version}\n",
                        skip_error="Cannot proceed - FM infrastructure migration required",
                        skip_hint="Run 'fm migrate' when ready",
                    )

                # Scenario 2: Bench needs migration -- nested after the infra update above, or
                # standalone when the infra was already up-to-date.
                if bench_needs_migration_flag and bench_arg and bench_version:

                    def record_bench_version() -> None:
                        set_bench_migration_version(bench_path, current_version)  # type: ignore[arg-type]

                    _prompt_and_run_migration(
                        output,
                        fm_config_manager,
                        warning=f"Bench '{bench_arg}' needs migration: v{bench_version} -> v{current_version}",
                        detail="This may modify bench configuration and services.",
                        detail_emoji="",
                        prompt=f"Migrate bench '{bench_arg}' now?",
                        choices=[
                            {"name": "Update now", "value": "update"},
                            {"name": f"Update later (run 'fm migrate {bench_arg}' when ready)", "value": "skip"},
                        ],
                        required_flag=f"'fm migrate {bench_arg}' (run migration explicitly)",
                        start_notice=f"\nMigrating bench '{bench_arg}'...\n",
                        start_emoji="🔄 ",
                        executor_kwargs={
                            "target_benches": [bench_arg],
                            "auto_proceed": True,
                            "on_failure": "rollback",
                        },
                        failure_error=f"Bench migration failed for '{bench_arg}'",
                        failure_hint=f"Please run 'fm migrate {bench_arg}' manually.",
                        record_version=record_bench_version,
                        success_notice=f"Bench '{bench_arg}' migrated to v{current_version}\n",
                        skip_warning=f"Skipped bench migration. Run 'fm migrate {bench_arg}' when ready.",
                        skip_note="Note: Bench may not work correctly until migrated.",
                        skip_error=f"Cannot {invoked_command} '{bench_arg}' - migration required",
                        skip_hint=f"Run 'fm migrate {bench_arg}' first",
                    )

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
from frappe_manager.commands.auth import auth
from frappe_manager.commands.bake import bake
from frappe_manager.commands.code import code

# Import extracted complex commands (Step 5)
from frappe_manager.commands.create import create
from frappe_manager.commands.delete import delete
from frappe_manager.commands.deploy import deploy, prune, switch
from frappe_manager.commands.info import info
from frappe_manager.commands.list import list as list_benches
from frappe_manager.commands.logs import logs
from frappe_manager.commands.migrate import migrate
from frappe_manager.commands.ngrok import ngrok
from frappe_manager.commands.reset import reset
from frappe_manager.commands.restart import restart
from frappe_manager.commands.maintenance import maintenance
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
app.command(name="maintenance")(maintenance)
app.command(name="auth")(auth)
app.command(name="ngrok")(ngrok)
app.command(name="migrate")(migrate)
app.command(name="bake", no_args_is_help=True)(bake)
app.command(name="deploy", no_args_is_help=True)(deploy)
app.command(name="switch", no_args_is_help=True)(switch)
app.command(name="prune", no_args_is_help=True)(prune)

# Export app and helpers for backward compatibility
__all__ = [
    "app",
    "app_callback",
    "check_bench_migration_required",
    "get_bench_arg_from_argv",
    "get_bench_arg_from_context",
]
