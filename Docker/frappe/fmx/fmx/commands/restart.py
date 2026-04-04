import os
import subprocess
import sys
import time
import json
import traceback
from pathlib import Path
from typing import Annotated, Optional, List, Any
import typer
from fmx.rq_controller import (
    control_rq_workers,
    wait_for_rq_workers_suspended,
    is_rq_suspended,
    ActionEnum,
)
from fmx.display import DisplayManager
from fmx.command_utils import validate_services
from fmx.cli import ServiceNameEnumFactory, execute_parallel_command, get_service_names_for_completion
from fmx.supervisor.api import (
    start_service as util_start_service,
    restart_service as util_restart_service,
)


def _set_common_site_config_key_value(
    key_name: str, value: Any, display: Optional[DisplayManager] = None, verbose: bool = False
):
    """Write a single key-value pair into common_site_config.json, preserving existing keys."""
    common_config_path = Path("/workspace/frappe-bench/sites/common_site_config.json")
    config = {}
    try:
        if common_config_path.exists():
            with open(common_config_path, 'r') as f:
                try:
                    config = json.load(f)
                except json.JSONDecodeError:
                    config = {}
        config[key_name] = value
        with open(common_config_path, 'w') as f:
            json.dump(config, f, indent=4)
        if verbose and display:
            display.info(f"Set {key_name} to {value} in {common_config_path}")
        return True
    except Exception as e:
        if verbose and display:
            display.warning(f"Could not write {key_name} to {common_config_path}: {e}")
        return False


command_name = "restart"

ServiceNamesEnum = ServiceNameEnumFactory()


def enable_maintenance_mode(display: DisplayManager):
    display.warning("Enabling maintenance mode...")
    if _set_common_site_config_key_value("maintenance_mode", 1, display=display):
        display.success("Maintenance mode enabled.")
    else:
        display.error("Failed to enable maintenance mode in common_site_config.json.")
        raise typer.Exit(code=1)


def disable_maintenance_mode(display: DisplayManager):
    display.warning("Disabling maintenance mode...")
    if _set_common_site_config_key_value("maintenance_mode", 0, display=display):
        display.success("Maintenance mode disabled.")
    else:
        display.error("Failed to disable maintenance mode in common_site_config.json.")


def _suspend_rq_workers(
    display: DisplayManager,
    wait_workers: bool,
    wait_workers_timeout: int,
    wait_workers_poll: int,
    debug: bool = False,
    skip_stale: bool = True,
    stale_timeout: int = 15,
) -> bool:
    """Set the rq:suspended flag in Redis and optionally wait for all workers to reach suspended state.

    Returns False if suspension fails or workers don't drain within timeout.
    """
    display.print("⏸️  Suspending RQ workers...")
    try:
        success = control_rq_workers(action=ActionEnum.suspend)

        if not success:
            display.error("Failed to suspend RQ workers.", exit_code=1)
            display.print("Aborting restart.")
            return False

        suspension_status = is_rq_suspended()

        if suspension_status is not True:
            display.error("Failed to verify RQ suspension flag in Redis.")
            display.print("Aborting restart.")
            return False

        if wait_workers:
            display.print("\nWaiting for RQ workers to complete their current jobs...")
            if not wait_for_rq_workers_suspended(
                timeout=wait_workers_timeout,
                poll_interval=wait_workers_poll,
                skip_stale=skip_stale,
                stale_timeout=stale_timeout,
            ):
                display.error("Workers did not become idle within the timeout period.")
                display.print("Aborting restart to avoid interrupting jobs.")
                control_rq_workers(action=ActionEnum.resume)
                return False

    except Exception as e:
        display.error(f"An unexpected error occurred during worker suspension or verification: {e}")
        if debug:
            traceback.print_exc()
        return False

    return True


def _resume_rq_workers(display: DisplayManager) -> bool:
    if not is_rq_suspended():
        return True

    display.print("▶️  Resuming RQ workers...")
    try:
        success = control_rq_workers(action=ActionEnum.resume)

        if not success:
            display.warning("Failed to resume RQ workers. Workers may remain suspended.")
            return False

    except Exception as e:
        display.error(f"Error resuming workers: {e}")
        traceback.print_exc(file=sys.stderr)
        return False

    return True


def _run_migration(display: DisplayManager, migrate_timeout: int, migrate_command: Optional[List[str]] = None) -> bool:
    """Run bench migrate with timeout and real-time output.

    Logic:
    1. Executes 'bench migrate' (or custom command) from /workspace/frappe-bench directory
    2. Shows real-time output during migration
    3. Applies specified timeout to prevent hanging
    4. Returns success/failure status

    Args:
        display: DisplayManager for output
        migrate_timeout: Timeout in seconds
        migrate_command: Custom migrate command (default: ["bench", "migrate", "--skip-failing"])

    Returns:
        True if migration succeeded, False to abort restart
    """
    if migrate_command is None:
        migrate_command = ["bench", "migrate", "--skip-failing"]

    full_command = ' '.join(migrate_command)
    display.print(f"🔄 Running: cd /workspace/frappe-bench && {full_command}")
    display.dimmed(f"Migration timeout: {migrate_timeout}s")

    try:
        start_time = time.time()

        process = subprocess.Popen(
            migrate_command,
            cwd="/workspace/frappe-bench",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
            universal_newlines=True,
            env={"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", **dict(os.environ)},
        )

        output_lines = []
        try:
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    line = output.rstrip('\r\n')
                    if line:
                        display.print(f"  {line}")
                        is_progress_bar = '[' in line and ']' in line and '%' in line
                        if not is_progress_bar:
                            output_lines.append(line)

                if time.time() - start_time > migrate_timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise subprocess.TimeoutExpired(["bench", "migrate"], migrate_timeout)

        finally:
            if process.stdout:
                process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

        elapsed_time = time.time() - start_time
        return_code = process.returncode

        if return_code == 0:
            display.success(f"Migration completed successfully in {elapsed_time:.1f}s")
            return True
        else:
            display.error(f"Migration failed with exit code {return_code}")
            if output_lines:
                display.print("Recent migration output:")
                for line in output_lines[-30:]:
                    display.print(f"  {line}")
            return False

    except subprocess.TimeoutExpired:
        display.error(f"Migration timed out after {migrate_timeout}s")
        display.print("Consider increasing --migrate-timeout if migration needs more time.")
        return False
    except FileNotFoundError:
        display.error("bench command not found. Ensure you're running from within the Frappe environment.")
        return False
    except Exception as e:
        display.error(f"Unexpected error during migration: {e}")
        return False


def _run_migrate_flow(
    display: DisplayManager,
    services_to_target: List[str],
    suspension_needed: bool,
    wait_workers: bool,
    wait_workers_timeout: int,
    wait_workers_poll: int,
    migrate_timeout: int,
    migrate_command: Optional[List[str]],
    wait: bool,
    maintenance_on_drain: bool = False,
    maintenance_on_migrate: bool = False,
    force_kill_timeout: Optional[int] = None,
    debug: bool = False,
    skip_stale: bool = True,
    stale_timeout: int = 15,
):
    """Execute zero-downtime migration flow with proper phase transitions.

    Maintenance mode is opt-in per phase:
    - drain:   Enable during RQ suspension and drain (disable after drain if migrate not requested)
    - migrate: Enable during bench migrate + service restart (disable after restart)

    Flow:
    1. [drain]   If maintenance_on_drain → enable; suspend RQ workers, wait for drain
    2. [transition] If drain-only (not migrate) → disable before migration
    3. [migrate] If maintenance_on_migrate and not already on → enable; run bench migrate
    4. On success: restart all services; disable maintenance if maintenance_on_migrate
    5. On failure: resume RQ + start all services (non-running); finally block cleans up

    Non-worker services (web, nginx, redis) never stop during migration.
    """
    maintenance_enabled = False
    try:
        # PHASE 1: DRAIN (RQ suspension and drain)
        if maintenance_on_drain:
            enable_maintenance_mode(display)
            maintenance_enabled = True

        if suspension_needed:
            if not _suspend_rq_workers(
                display,
                wait_workers,
                wait_workers_timeout,
                wait_workers_poll,
                debug,
                skip_stale=skip_stale,
                stale_timeout=stale_timeout,
            ):
                raise typer.Exit(code=1)

        # TRANSITION: drain → migrate
        # Drain is done; if migrate phase doesn't need maintenance, turn it off now
        if maintenance_enabled and not maintenance_on_migrate:
            disable_maintenance_mode(display)
            maintenance_enabled = False

        # PHASE 2: MIGRATE (bench migrate)
        if maintenance_on_migrate and not maintenance_enabled:
            enable_maintenance_mode(display)
            maintenance_enabled = True

        if not _run_migration(display, migrate_timeout, migrate_command):
            _handle_migrate_failure(display, services_to_target, suspension_needed, wait)
            if suspension_needed:
                _resume_rq_workers(display)
            raise typer.Exit(code=1)

        # Resume workers BEFORE restarting services so they can start properly
        if suspension_needed:
            _resume_rq_workers(display)

        # PHASE 3: SERVICE RESTART - handled in success handler
        maintenance_disabled_by_handler = _handle_migrate_success(
            display,
            services_to_target,
            wait,
            force_kill_timeout,
            maintenance_on_migrate,
        )
        if maintenance_disabled_by_handler:
            maintenance_enabled = False

    finally:
        if maintenance_enabled:
            disable_maintenance_mode(display)


def _handle_migrate_success(
    display: DisplayManager,
    services_to_target: List[str],
    wait: bool,
    force_kill_timeout: Optional[int],
    maintenance_on_migrate: bool = False,
) -> bool:
    """Handle migration success - full service restart.

    Restarts all supervisor services in parallel, then disables maintenance
    mode if it was enabled for the migrate phase.

    Returns:
        True if maintenance was disabled by this function, False otherwise
    """
    display.success("Migration succeeded. Restarting all services...")

    execute_parallel_command(
        services_to_target,
        util_restart_service,
        action_verb="restarting",
        show_progress=True,
        wait=wait,
        force_kill_timeout=force_kill_timeout,
    )

    if maintenance_on_migrate:
        disable_maintenance_mode(display)
        return True

    return False


def _handle_migrate_failure(
    display: DisplayManager,
    services_to_target: List[str],
    suspension_needed: bool,
    wait: bool,
):
    """Handle migration failure recovery.

    Recovery steps:
    1. Start all non-running services to ensure full availability

    Uses start (not restart) to avoid disrupting already-running services.
    Site stays up - only ensures all services are running.

    Note: RQ resume is handled by the caller after this function returns.
    """
    display.error("Migration failed. Recovering — starting all services...")

    execute_parallel_command(
        services_to_target,
        util_start_service,
        action_verb="starting",
        show_progress=True,
        wait=wait,
    )


def command(
    ctx: typer.Context,
    service_names: Annotated[
        Optional[List[ServiceNamesEnum]],
        typer.Argument(
            help="Name(s) of the service(s) to restart. If omitted, targets ALL running services.",
            autocompletion=get_service_names_for_completion,
            show_default=False,
        ),
    ] = None,
    suspend_rq: Annotated[
        bool,
        typer.Option(
            "--suspend-rq",
            help="Suspend RQ workers via Redis flag before restarting. "
            "Without --migrate: workers suspended before all services stop. "
            "With --migrate: workers suspended and drained instead of stopping services. "
            "Combine with --wait-workers to ensure jobs complete before migration runs.",
        ),
    ] = False,
    migrate: Annotated[
        bool,
        typer.Option(
            "--migrate",
            help="Run migration without stopping all services first (default: 'bench migrate --skip-failing'). "
            "RQ workers are suspended (if --suspend-rq) and drained (if --wait-workers), "
            "then migration runs. Non-worker services (web, nginx, redis) stay up. "
            "On success: all services restart. "
            "On failure: RQ resumes and all services are started (non-running ones only). "
            "Recommended: --suspend-rq --wait-workers for safe zero-job-loss migration. "
            "Use --migrate-command to customize the migration command.",
        ),
    ] = False,
    migrate_timeout: Annotated[
        int,
        typer.Option(
            "--migrate-timeout",
            help="Timeout (seconds) for bench migrate operation (default: 300).",
        ),
    ] = 300,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for the final supervisor restart operations to complete before returning.",
        ),
    ] = True,
    wait_workers: Annotated[
        bool,
        typer.Option(
            "--wait-workers",
            help="Suspend RQ workers and wait for them to finish their current jobs before restarting. Implies --suspend-rq.",
            is_flag=True,
        ),
    ] = False,
    wait_workers_timeout: Annotated[
        int,
        typer.Option(
            "--wait-workers-timeout",
            help="Timeout (seconds) for --wait-workers (default: 300).",
        ),
    ] = 300,
    wait_workers_poll: Annotated[
        int,
        typer.Option(
            "--wait-workers-poll",
            help="Polling interval (seconds) for --wait-workers (default: 5).",
        ),
    ] = 5,
    skip_stale_workers: Annotated[
        bool,
        typer.Option(
            "--skip-stale-workers/--no-skip-stale-workers",
            help="When used with --wait-workers, skip workers that remain idle after suspension is set. "
            "Workers idle longer than --skip-stale-timeout seconds are treated as dead and ignored.",
        ),
    ] = True,
    skip_stale_timeout: Annotated[
        int,
        typer.Option(
            "--skip-stale-timeout",
            help="Seconds after which an idle post-suspension worker is considered stale and skipped (default: 15). "
            "Used with --skip-stale-workers.",
        ),
    ] = 15,
    force_kill_timeout: Annotated[
        Optional[int],
        typer.Option(
            "--force-kill-timeout",
            help="Timeout (seconds) after which stubborn non-worker processes will be forcefully killed during restart.",
        ),
    ] = None,
    migrate_command: Annotated[
        Optional[str],
        typer.Option(
            "--migrate-command",
            help="Custom migrate command to run (default: 'bench migrate --skip-failing'). "
            "Example: 'bench migrate' or 'bench --site mysite.localhost migrate'.",
        ),
    ] = None,
    maintenance_mode: Annotated[
        Optional[List[str]],
        typer.Option(
            "--maintenance-mode",
            help=(
                "Enable maintenance mode for specific phases: "
                "'drain' (during RQ worker drain) and/or 'migrate' (during bench migrate + service restart). "
                "Example: --maintenance-mode drain --maintenance-mode migrate"
            ),
            show_default=False,
        ),
    ] = None,
):
    """
    Restart services with optional RQ worker coordination, migration, and maintenance mode.

    Performs supervisor-based restart with optional Redis worker suspension.
    Can run bench migrate between stop and start phases.
    Can optionally wait for workers to complete jobs before restarting.
    Always attempts to resume workers after restart completion.

    Maintenance mode can be enabled for 'drain' (RQ drain phase) and/or 'migrate'
    (bench migrate + restart phase) using --maintenance-mode.
    Example: --maintenance-mode drain --maintenance-mode migrate
    """
    display: DisplayManager = ctx.obj['display']
    debug: bool = ctx.obj.get('debug', False)

    valid_mm_values = {"drain", "migrate"}
    maintenance_set = set(maintenance_mode or [])
    if not maintenance_set.issubset(valid_mm_values):
        display.error(
            f"Invalid value(s) for --maintenance-mode: {', '.join(maintenance_set - valid_mm_values)}. "
            f"Allowed values: drain, migrate."
        )
        raise typer.Exit(code=1)

    maintenance_on_drain = "drain" in maintenance_set
    maintenance_on_migrate = "migrate" in maintenance_set

    suspension_needed = suspend_rq or wait_workers

    if maintenance_on_drain and not suspension_needed:
        display.warning(
            "--maintenance-mode drain has no effect without --suspend-rq or --wait-workers. "
            "Maintenance mode will not be enabled for the drain phase."
        )
        maintenance_on_drain = False

    if maintenance_on_migrate and not migrate:
        display.warning(
            "--maintenance-mode migrate has no effect without --migrate. "
            "Maintenance mode will not be enabled for the migrate phase."
        )
        maintenance_on_migrate = False

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "restart")
    if not valid:
        return

    wait_desc = "(with wait)" if wait else "(without wait)"
    display.dimmed(f"\nAttempting to restart {target_desc} {wait_desc}...")

    migrate_command_list: Optional[List[str]] = None
    if migrate_command:
        migrate_command_list = migrate_command.split()

    if migrate:
        if not suspension_needed:
            display.warning(
                "Running migration without suspending RQ workers. "
                "Active jobs may fail if migration changes the database schema. "
                "Consider using --suspend-rq --wait-workers for safe migration."
            )

        try:
            _run_migrate_flow(
                display,
                services_to_target,
                suspension_needed,
                wait_workers,
                wait_workers_timeout,
                wait_workers_poll,
                migrate_timeout,
                migrate_command_list,
                wait,
                maintenance_on_drain=maintenance_on_drain,
                maintenance_on_migrate=maintenance_on_migrate,
                force_kill_timeout=force_kill_timeout,
                debug=debug,
                skip_stale=skip_stale_workers,
                stale_timeout=skip_stale_timeout,
            )
        finally:
            if suspension_needed:
                _resume_rq_workers(display)
            display.print("\nRestart sequence complete.")
    else:
        maintenance_enabled = False
        try:
            if maintenance_on_drain and suspension_needed:
                enable_maintenance_mode(display)
                maintenance_enabled = True

            if suspension_needed:
                if not _suspend_rq_workers(
                    display,
                    wait_workers,
                    wait_workers_timeout,
                    wait_workers_poll,
                    debug,
                    skip_stale=skip_stale_workers,
                    stale_timeout=skip_stale_timeout,
                ):
                    raise typer.Exit(code=1)

            if maintenance_enabled:
                disable_maintenance_mode(display)
                maintenance_enabled = False

            execute_parallel_command(
                services_to_target,
                util_restart_service,
                action_verb="restarting",
                show_progress=True,
                wait=wait,
                wait_workers=wait_workers,
                force_kill_timeout=force_kill_timeout,
            )

        finally:
            if suspension_needed:
                _resume_rq_workers(display)
            if maintenance_enabled:
                disable_maintenance_mode(display)
            display.print("\nRestart sequence complete.")
