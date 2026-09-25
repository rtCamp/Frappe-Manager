import builtins
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
    MIGRATION_COMMANDS,
    OBSERVE_ONLY_COMMANDS,
    STABLE_APP_BRANCH_MAPPING_LIST,
    STOCK_IMAGE_PREFETCH_SKIP_COMMANDS,
    EnableDisableOptionsEnum,
)
from frappe_manager.commands.gating import (
    FMGroup,
    command_args,
    command_path,
    tolerates_broken_host,
    will_print_help,
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
from frappe_manager.migration_manager.migration_executor import MigrationExecutor
from frappe_manager.migration_manager.version import Version
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
from frappe_manager.utils.helpers import get_current_fm_version
from frappe_manager.utils.site import pull_docker_images, validate_sitename


def get_bench_arg_from_context(ctx: typer.Context) -> str | None:
    """
    Extract bench/site name from command context.
    Commands use different parameter names (benchname, sitename, bench_name).

    Only useful on a *subcommand* context. On the group context built for ``app_callback``
    Click populates ``ctx.params`` with the group's own options only, so this returns None
    there and the caller falls back to :func:`get_bench_arg_from_args`.
    """
    return ctx.params.get("benchname") or ctx.params.get("sitename") or ctx.params.get("bench_name")


def get_bench_arg_from_args(args: "builtins.list[str]") -> str | None:
    """The bench/site name among a command's own arguments.

    ``app_callback`` is a group callback: Click resolves the subcommand and builds its context
    only after the callback returns, so the subcommand's ``benchname`` parameter does not exist
    yet. ``args`` is what the root group recorded (:func:`gating.command_args`) -- everything
    after the resolved command, with fm's global options already parsed off by Click.

    The first token wins unless it is a flag or a filesystem path. Anything more clever would
    need the subcommand's own parser; a missed name only means the callback gate stays quiet,
    because every bench command still re-checks via ``check_bench_migration_required``.
    """
    for token in args:
        if token.startswith(("-", "/", "~")):
            break
        # The bench half only: this feeds `CLI_BENCHES_DIRECTORY / bench_arg` below, and a
        # `bench/site` address would silently become a nested non-existent path, taking the
        # migration gate quiet with it.
        return token.split("/", 1)[0]

    return None


def check_bench_migration_required(bench_name: str | None) -> None:
    from frappe_manager.migration_manager.bench_migration_state import bench_needs_migration

    if not bench_name:
        return

    bench_path = CLI_BENCHES_DIRECTORY / bench_name

    # A DIRECTORY is not a bench: without its config the version probe reads 0.0.0 and this
    # demanded `fm migrate` for something that was never created. The caller's own bench load
    # raises `BenchConfigNotFoundError`, which names the file and the recovery. Same guard as the
    # callback gate in `app_callback`.
    if not (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists():
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
    record_version: Callable[[], None] | None,
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
    (``migrate_global_services`` vs ``target_benches``), where the new version is recorded
    (the infra gate passes ``record_version=None`` because the executor itself is the sole
    stamper of the services-tier ledger, while the bench gate still stamps via
    ``set_bench_migration_version(bench_path, ...)`` -- hence the callback), and the refusal
    text: the bench gate emits an extra "skipped"/"may not work" notice (``skip_warning`` /
    ``skip_note``) that the infrastructure gate does not.
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

        if record_version is not None:
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


app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", cls=FMGroup)
install(app)

# Rich help panels for `fm --help`, grouped by the shape of the address the command takes (see
# `frappe_manager/commands/arguments.py` for the underlying `Annotated` aliases). Every top-level
# command and sub-app below carries one of these explicitly: `rich_utils.py` puts any command left
# without a panel into an unlabelled "Commands" box that renders BEFORE all named panels, so a
# single omission would silently jump that command above this grouping. Panel order follows first
# appearance while registering commands (`typer/core.py` keeps declaration order, not alphabetical),
# so this order matches the order the `app.command(...)` block below registers them in.
_PANEL_BENCH = "BENCH address commands"
_PANEL_SITE = "BENCH/SITE address commands"
_PANEL_DOMAIN = "BENCH/DOMAIN address commands"
_PANEL_GLOBAL = "GLOBAL address commands"

# `apps`, `domain`, `tools` and `monitor` commands call `check_bench_migration_required`
# (defined above) at their own module's import time, so these must be imported after that
# function exists in this module's namespace -- unlike `self`/`services`/`ssl` above, which do
# not call it.
from frappe_manager.commands.apps import apps_app
from frappe_manager.commands.domain import domain_app
from frappe_manager.commands.telemetry import telemetry_app
from frappe_manager.commands.tools import tools_app

app.add_typer(services_app, name="services", help="Handle global services.", rich_help_panel=_PANEL_GLOBAL)
app.add_typer(
    self_app,
    name="self",
    help="Perform operations related to [bold][blue]fm[/bold][/blue] itself.",
    rich_help_panel=_PANEL_GLOBAL,
)
app.add_typer(ssl_app, name="ssl", help="Perform operations related to ssl.", rich_help_panel=_PANEL_DOMAIN)
app.add_typer(apps_app, name="apps", help="Manage the apps installed on a bench.", rich_help_panel=_PANEL_SITE)
app.add_typer(domain_app, name="domain", help="Manage a bench's alias domains.", rich_help_panel=_PANEL_DOMAIN)
app.add_typer(tools_app, name="tools", help="Manage a bench's admin tools.", rich_help_panel=_PANEL_SITE)
app.add_typer(
    telemetry_app,
    name="telemetry",
    help="Manage a bench's telemetry (APM) backends.",
    rich_help_panel=_PANEL_SITE,
)



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
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Machine-readable output: every output event is written to stdout as one JSON line (JSONL), as it happens. Implies --non-interactive.",
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

    from frappe_manager.output_manager import get_global_output_handler, set_global_output_handler

    if log_level:
        level_name = log_level.upper()

        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        if level_name not in valid_levels:
            output = get_global_output_handler()
            output.display_error(f"Invalid log level: {log_level}. Must be one of: {', '.join(valid_levels).lower()}")
            raise typer.Exit(1)
    elif verbose:
        level_name = "INFO"
    else:
        level_name = "WARNING"

    ctx.obj["log_level"] = level_name
    ctx.obj["verbose"] = verbose or level_name in ["INFO", "DEBUG"]
    ctx.obj["non_interactive"] = non_interactive or json_output
    ctx.obj["json"] = json_output

    # Upgrade global output handler to LoggingOutputHandler now that we have CLI args.
    # --json swaps the underlying handler FIRST, so file logging still wraps it: rich
    # rendering is replaced by one JSON line per event on stdout, and prompts raise
    # NonInteractiveError instead of corrupting the machine stream (hence the implied
    # --non-interactive above).
    if json_output:
        from frappe_manager.output_manager import JSONOutputHandler

        basic_handler = JSONOutputHandler(verbose=ctx.obj["verbose"], stream=sys.stdout)
    else:
        basic_handler = get_global_output_handler()
    set_global_output_handler(basic_handler)

    output = get_global_output_handler()
    output.set_interactive_mode(non_interactive_flag=non_interactive or json_output)

    help_called = will_print_help(ctx)
    ctx.obj["is_help_called"] = help_called

    if not help_called:
        # The file-logging wrapper is built HERE, not above: constructing it opens
        # CLI_DIR/logs/fm.log, which creates the fm home as a side effect. Doing that before the
        # help gate meant `fm start --help` wrote a log directory onto a machine that had never
        # run fm -- and, because CLI_DIR then existed, the creation branch below never ran and
        # the benches directory was never made.
        set_global_output_handler(LoggingOutputHandler(basic_handler))
        output = get_global_output_handler()
        output.set_interactive_mode(non_interactive_flag=non_interactive or json_output)

        with spinner(output, "Working"):
            created_home = not CLI_DIR.exists()
            if not CLI_DIR.is_dir():
                if CLI_DIR.exists():
                    output.exit(f"{CLI_DIR} exists but is not a directory! Aborting!")
                output.print(f"fm directory doesn't exists! Created at -> {CLI_DIR!s}")
            CLI_DIR.mkdir(parents=True, exist_ok=True)
            CLI_BENCHES_DIRECTORY.mkdir(parents=True, exist_ok=True)

            global logger
            console_level = level_name if ctx.obj["verbose"] else None

            try:
                fm_config_manager: FMConfigManager = FMConfigManager.import_from_toml()
            except Exception as e:
                # A config fm cannot parse must not trap the commands that exist to clean up after
                # it. Every other command needs the config to do its job and still fails loudly.
                if not tolerates_broken_host(ctx):
                    raise
                output.warning(f"fm_config.toml could not be read ({e}); continuing with defaults.")
                fm_config_manager = FMConfigManager.defaults()
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

            # Two commands must survive a host whose docker is already gone, because that host is
            # exactly where fm's leftovers would otherwise be permanent: `ssl ca` only ever touches
            # host trust stores, and `self uninstall` reports the docker half as un-removable and
            # still deletes the files and the CA.
            if not tolerates_broken_host(ctx) and not DockerClient().server_running():
                output.exit("Docker daemon not running. Please start docker service")

            invoked_command = ctx.invoked_subcommand or "no-command"

            # The first-install prefetch warms the whole stock stack (frappe, nginx, two
            # redis, mariadb, nginx-proxy, mailpit, adminer) so a first `fm create` does not
            # stall halfway. Commands that never touch those containers are exempt: `fm bake`
            # builds an image and pulls the one base image it is told to build FROM, so
            # prefetching the stack is pure waste, and on a CI runner it is waste that gets
            # paid on every job. The teardown commands are exempt for a sharper reason: after a
            # successful uninstall there is no fm_config.toml, so running one again would read as
            # a first install and PULL the entire stack on the way to removing it. Observers are
            # exempt because they answer a question about state that does not exist yet: `fm list`
            # on a fresh host has nothing to list and must not spend minutes pulling images first.
            first_install = (
                not CLI_FM_CONFIG_PATH.exists()
                and not tolerates_broken_host(ctx)
                and command_path(ctx) not in OBSERVE_ONLY_COMMANDS
            )
            if first_install and invoked_command not in STOCK_IMAGE_PREFETCH_SKIP_COMMANDS:
                output.print("First installation detected. Pulling docker images...️", "🔍")

                completed_status = pull_docker_images()

                if not completed_status:
                    # Only the home THIS run created is cleaned up. Wiping CLI_DIR unconditionally
                    # deleted an existing install's logs and backups because one image pull failed.
                    if created_home and CLI_DIR.exists():
                        shutil.rmtree(CLI_DIR)
                    output.exit("Aborting. Not able to pull all required Docker images")

                # Stamp the services-tier ledger at the current version: everything this host
                # will ever manage is being created by THIS fm, so there is nothing to migrate
                # and the gates must read "current" from the very first command.
                fm_config_manager.set_system_migration_version(Version(get_current_fm_version()))

            from frappe_manager.migration_manager.migration_constants import (
                MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS,
                MIGRATION_CHECK_WHITELIST_COMMANDS,
            )

            # The command click resolved, as a full path ("start", "ssl add", "ssl ca status"),
            # recorded by the root group before this callback ran. Every whitelist below is keyed
            # by it.
            full_command = command_path(ctx) or invoked_command

            commands_skip_migration_check = MIGRATION_CHECK_WHITELIST_COMMANDS

            commands_skip_bench_migration = ["stop", "delete"] + MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS

            # Get bench argument if present. The group context never carries the subcommand's
            # benchname, so the command's own arguments are what resolve it here.
            bench_arg = get_bench_arg_from_context(ctx) or get_bench_arg_from_args(command_args(ctx))
            bench_path = CLI_BENCHES_DIRECTORY / bench_arg if bench_arg else None

            global_services_version = fm_config_manager.get_system_migration_version()
            current_version = Version(get_current_fm_version())
            infra_needs_migration = global_services_version < current_version

            bench_needs_migration_flag = False
            bench_version = None
            # A DIRECTORY is not a bench. Without its config the version probe reads 0.0.0, and
            # the gate offered to migrate something that was never created -- the half-built shape
            # a failed `fm create` leaves. Skipping here lets the command's own loader raise
            # `BenchConfigNotFoundError`, which names the bench, the missing file and the way out.
            bench_has_config = bool(bench_path and (bench_path / CLI_BENCH_CONFIG_FILE_NAME).exists())
            if bench_has_config and invoked_command not in commands_skip_bench_migration:
                bench_needs_migration_flag = bench_needs_migration(bench_path, current_version)
                if bench_needs_migration_flag:
                    bench_version = get_bench_migration_version(bench_path)

            should_check_migration = (
                # A teardown never has to migrate the state it is about to delete.
                not tolerates_broken_host(ctx)
                and invoked_command not in commands_skip_migration_check
                and full_command not in commands_skip_migration_check
            )

            if should_check_migration:
                output = get_global_output_handler()

                # Scenario 1: Infra needs migration. The gate either updates cleanly and falls
                # through, or raises typer.Exit(1) -- so the bench gate below still runs only
                # after a successful infra update, exactly as when it was nested inside it.
                if infra_needs_migration:

                    _prompt_and_run_migration(
                        output,
                        fm_config_manager,
                        warning=f"fm's global services & configuration need migration: v{global_services_version} -> v{current_version}",
                        detail="This updates the shared services (mariadb, nginx-proxy) and fm's own config",
                        detail_emoji="  ",
                        prompt="How would you like to proceed?",
                        choices=[
                            {"name": "Update now (recommended)", "value": "update"},
                            {"name": "Update later (run 'fm services migrate' when ready)", "value": "skip"},
                        ],
                        required_flag="'fm services migrate' (run migration explicitly)",
                        start_notice="\n🔄 Updating fm's global services & configuration...\n",
                        start_emoji="",
                        executor_kwargs={
                            "migrate_global_services": True,
                            "auto_proceed": True,
                            "on_failure": "rollback",
                        },
                        failure_error="Global services & configuration update failed",
                        failure_hint="Please run 'fm services migrate' manually to fix.",
                        record_version=None,
                        success_notice=f"Global services & configuration updated to v{current_version}\n",
                        skip_error="Cannot proceed - fm's global services & configuration need migration",
                        skip_hint="Run 'fm services migrate' when ready",
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

            # Every state-touching command holds the host lock SHARED for its lifetime: the
            # grip means "don't migrate under me". Shared grips never contend with each
            # other, so daily life is unchanged; a running migration (which holds it
            # EXCLUSIVE in MigrationExecutor.execute) makes this refuse instead. Taken AFTER
            # the gate and skipped for the migration commands, because a process conflicts
            # with its own grips: the gate's inline migration takes the exclusive grip and
            # releases it before this line runs. Pure observers hold NOTHING -- they must
            # keep working mid-migration (see OBSERVE_ONLY_COMMANDS, which also keeps them
            # from auto-starting the stack they are merely looking at).
            if full_command not in MIGRATION_COMMANDS and full_command not in OBSERVE_ONLY_COMMANDS:
                from frappe_manager.utils import process_lock

                host_lock = process_lock.acquire(
                    process_lock.migration_lock_path(), exclusive=False, holder=full_command
                )
                if host_lock is None:
                    culprit = process_lock.read_holder(process_lock.migration_lock_path()) or "a migration"
                    output.exit(f"{culprit} is in progress on this host; wait for it to finish and retry.")
                # Referenced for the command's whole lifetime; the grip dies with the process.
                ctx.obj["host_lock"] = host_lock

            # The FULL command path ("services migrate", not just the group name): the
            # services manager gates its pre-rename escape hatch and its auto-start
            # behavior on which command is running, and the group name alone cannot tell
            # `fm services migrate` (must run against an old install) from
            # `fm services start` (must not).
            services_manager: ServicesManager = ServicesManager(
                verbose=ctx.obj["verbose"],
                invoked_subcommand=full_command,
            )

            services_manager.init()

            # The global stack (mariadb, nginx-proxy) is a bench RUNTIME
            # dependency: a bench's schema lives in mariadb and the proxy is its only
            # route in, so a command that acts on a running bench needs both up. `bake`
            # touches neither -- it never loads a Bench, a database manager or the proxy,
            # it only builds an image -- so it needs the stack neither started NOR
            # created. That is what makes it usable on a throwaway runner: creation mints
            # DB passwords, allocates a subnet and pulls mariadb + nginx-proxy, all of
            # which a build discards. `migrate` owns its own service lifecycle, so the
            # stack is ensured for it but not started. `switch` is deliberately absent
            # from both lists: it runs bench migrate against mariadb.
            # `ssl ca` and `self uninstall` join `bake` here for a different reason: creating (let
            # alone starting) the shared stack on the way to tearing it down would resurrect the
            # very services being removed, and on a never-created host it would build them from
            # scratch just to delete them.
            if invoked_command != "bake" and not tolerates_broken_host(ctx):
                try:
                    services_manager.entrypoint_checks(start=invoked_command != "migrate")
                except ServicesNotCreated as e:
                    services_manager.remove_itself()
                    output.exit(f"Not able to create services. {e}")

            ctx.obj["services"] = services_manager
            ctx.obj["fm_config_manager"] = fm_config_manager


from frappe_manager.commands.auth import auth
from frappe_manager.commands.bake import bake
from frappe_manager.commands.code import code
from frappe_manager.commands.compose import compose
from frappe_manager.commands.create import create
from frappe_manager.commands.delete import delete
from frappe_manager.commands.deploy import switch
from frappe_manager.commands.info import info
from frappe_manager.commands.list import list as list_benches
from frappe_manager.commands.logs import logs
from frappe_manager.commands.maintenance import maintenance
from frappe_manager.commands.migrate import migrate
from frappe_manager.commands.ngrok import ngrok
from frappe_manager.commands.prune import prune
from frappe_manager.commands.reset import reset
from frappe_manager.commands.restart import restart
from frappe_manager.commands.shell import shell
from frappe_manager.commands.start import start
from frappe_manager.commands.stop import stop
from frappe_manager.commands.update import update

# Register all commands with the app, grouped and ordered by rich_help_panel (see the constants
# above): every command carries an explicit panel, and their relative order here is what decides
# the panel order in `fm --help`.
app.command(name="start", rich_help_panel=_PANEL_BENCH)(start)
app.command(name="stop", rich_help_panel=_PANEL_BENCH)(stop)
app.command(name="code", rich_help_panel=_PANEL_BENCH)(code)
app.command(name="logs", rich_help_panel=_PANEL_BENCH)(logs)
app.command(
    name="compose",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    rich_help_panel=_PANEL_BENCH,
)(compose)
app.command(name="info", rich_help_panel=_PANEL_BENCH)(info)
app.command(name="restart", rich_help_panel=_PANEL_BENCH)(restart)
app.command(name="migrate", rich_help_panel=_PANEL_BENCH)(migrate)
app.command(name="bake", no_args_is_help=True, rich_help_panel=_PANEL_BENCH)(bake)
app.command(name="switch", no_args_is_help=True, rich_help_panel=_PANEL_BENCH)(switch)
app.command(name="prune", no_args_is_help=True, rich_help_panel=_PANEL_BENCH)(prune)
app.command(name="create", no_args_is_help=True, rich_help_panel=_PANEL_SITE)(create)
app.command(name="delete", rich_help_panel=_PANEL_SITE)(delete)
app.command(
    name="shell",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    rich_help_panel=_PANEL_SITE,
)(shell)
app.command(name="update", no_args_is_help=True, rich_help_panel=_PANEL_SITE)(update)
app.command(name="reset", rich_help_panel=_PANEL_SITE)(reset)
app.command(name="maintenance", rich_help_panel=_PANEL_SITE)(maintenance)
app.command(name="auth", rich_help_panel=_PANEL_SITE)(auth)
app.command(name="ngrok", rich_help_panel=_PANEL_DOMAIN)(ngrok)
app.command(name="list", rich_help_panel=_PANEL_GLOBAL)(list_benches)

__all__ = [
    "app",
    "app_callback",
    "check_bench_migration_required",
    "get_bench_arg_from_args",
    "get_bench_arg_from_context",
]
