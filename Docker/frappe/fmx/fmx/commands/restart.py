import os
import subprocess
import sys
import time
import json
import traceback
from pathlib import Path
from typing import Annotated, Optional, List, Any
import typer
from fmx.rq_controller import control_rq_workers, check_rq_suspension, wait_for_rq_workers_suspended, ActionEnum
from fmx.display import DisplayManager
from fmx.command_utils import validate_services
from fmx.cli import ServiceNameEnumFactory, execute_parallel_command, get_service_names_for_completion
from fmx.supervisor.api import (
    signal_service_workers as util_signal_service_workers,
    stop_service as util_stop_service,
    start_service as util_start_service,
    restart_service as util_restart_service,
)


def _set_common_site_config_key_value(key_name: str, value: Any, verbose: bool = False):
    """Set a specific key's value in common_site_config.json."""
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
        if verbose:
            print(f"[dim]Set {key_name} to {value} in {common_config_path}[/dim]")
        return True
    except Exception as e:
        if verbose:
            print(f"[yellow]Warning:[/yellow] Could not write {key_name} to {common_config_path}: {e}")
        return False


command_name = "restart"

ServiceNamesEnum = ServiceNameEnumFactory()


def enable_maintenance_mode(display: DisplayManager):
    display.print("[yellow]Enabling maintenance mode...[/yellow]")
    if _set_common_site_config_key_value("maintenance_mode", 1):
        display.success("Maintenance mode enabled.")
    else:
        display.error("Failed to enable maintenance mode in common_site_config.json.")
        raise typer.Exit(code=1)


def disable_maintenance_mode(display: DisplayManager):
    display.print("[yellow]Disabling maintenance mode...[/yellow]")
    if _set_common_site_config_key_value("maintenance_mode", 0):
        display.success("Maintenance mode disabled.")
    else:
        display.error("Failed to disable maintenance mode in common_site_config.json.")
        # Do not exit, just warn


def _suspend_rq_workers(
    display: DisplayManager,
    wait_workers: Optional[bool],
    wait_workers_timeout: int,
    wait_workers_poll: int,
    wait_workers_verbose: bool,
) -> bool:
    """Suspend RQ workers via Redis flag and optionally wait for completion.

    Logic:
    1. Sets 'rq:suspended' flag in Redis using control_rq_workers
    2. Verifies the flag was set correctly using check_rq_suspension
    3. If wait_workers=True: waits for workers to reach suspended state
    4. Returns success/failure status for the entire suspension process

    Returns:
        True if suspension completed successfully, False to abort restart
    """
    display.print("⏸️  Suspending RQ workers via Redis flag...")
    try:
        success = control_rq_workers(action=ActionEnum.suspend)

        if not success:
            display.error("Failed to suspend RQ workers via Redis.", exit_code=1)
            display.print("Check logs above for details from rq_controller.")
            display.print("Aborting restart.")
            return False
        else:
            display.success("RQ workers suspended via Redis flag.")

            display.dimmed("Verifying suspension status...")
            suspension_status = check_rq_suspension()

            if suspension_status is True:
                display.success("Verification successful: RQ suspension flag is set in Redis.")
            elif suspension_status is False:
                display.error(
                    "Verification failed: RQ suspension flag was NOT found in Redis after attempting to set it."
                )
                display.print("Aborting restart.")
                return False
            else:
                display.error("Could not verify suspension status due to an error during the check.")
                display.print("Check logs above for details from rq_controller check.")
                display.print("Aborting restart.")
                return False

            if wait_workers is True:
                display.print("\n[cyan]Waiting for RQ workers to complete their current jobs...[/cyan]")
                if not wait_for_rq_workers_suspended(
                    timeout=wait_workers_timeout, poll_interval=wait_workers_poll, verbose=wait_workers_verbose
                ):
                    display.error("Workers did not become idle within the timeout period.")
                    display.print("Aborting restart to avoid interrupting jobs.")
                    control_rq_workers(action=ActionEnum.resume)
                    return False

    except Exception as e:
        display.error(f"An unexpected error occurred during worker suspension or verification: {e}")
        traceback.print_exc()
        return False

    return True


def _signal_workers_for_graceful_shutdown(display: DisplayManager, services_to_target: List[str]):
    """Signal workers for graceful shutdown without waiting.

    Logic:
    1. Iterates through all target services
    2. Uses util_signal_service_workers to send graceful exit signals
    3. Logs which workers were signaled in each service
    4. Designed to work with bench-wrapper.sh monitor process
    """
    display.print("Signaling workers for graceful shutdown...")
    try:
        for service_name in services_to_target:
            signaled_workers = util_signal_service_workers(service_name)
            if signaled_workers:
                display.success(f"Signaled workers in {display.highlight(service_name)}: {', '.join(signaled_workers)}")

    except Exception as e:
        display.error(f"Error during worker signaling: {e}")
        display.warning("Proceeding with restart despite signaling error.")


def _resume_rq_workers(display: DisplayManager) -> bool:
    """Resume RQ workers by removing Redis suspension flag.

    Logic:
    1. Calls control_rq_workers with ActionEnum.resume
    2. Handles both success and failure cases gracefully
    3. Provides user feedback about resume status
    4. Used in finally block to ensure cleanup

    Returns:
        True if resume succeeded, False if failed (non-fatal)
    """
    display.print("▶️  Resuming RQ workers via Redis flag...")
    try:
        success = control_rq_workers(action=ActionEnum.resume)

        if not success:
            display.warning("Failed to resume RQ workers via Redis flag. Check logs above.")
            display.warning(
                "You may need to manually remove the 'rq:suspended' key in Redis if workers remain suspended."
            )
            return False

    except Exception as e:
        display.error(f"An unexpected error occurred while trying to call rq_controller to resume workers: {e}")
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
        migrate_command: Custom migrate command (default: ["bench", "migrate", "--skip-failing-patches"])

    Returns:
        True if migration succeeded, False to abort restart
    """
    if migrate_command is None:
        migrate_command = ["bench", "migrate", "--skip-failing-patches"]

    display.print(f"🔄 Running {' '.join(migrate_command)}...")
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
                        print(f"  {line}", flush=True)
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
                for line in output_lines[-10:]:
                    print(f"  {line}")
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
    wait_workers: Optional[bool],
    wait_workers_timeout: int,
    wait_workers_poll: int,
    wait_workers_verbose: bool,
    migrate_timeout: int,
    migrate_command: Optional[List[str]],
    wait: bool,
    maintenance_phases: set,
    force_kill_timeout: Optional[int],
):
    """Execute zero-downtime migration flow with proper phase transitions.

    Phases:
    - stop: Maintenance during RQ suspension and drain
    - migrate: Maintenance during bench migrate
    - start: Maintenance during service restart (handled in success path)

    Flow:
    1. [stop phase] Enable maintenance, suspend RQ workers, wait for drain
    2. [stop→migrate transition] Keep/disable maintenance based on next phase
    3. [migrate phase] Enable maintenance (if not already), run bench migrate
    4. [migrate→start transition] Handled in success/failure handlers
    5. On success: resume RQ + restart all services
    6. On failure: resume RQ + restart workers only (site stays up)

    Non-worker services (web, nginx, redis) never stop during migration.
    """
    maintenance_enabled = False
    maintenance_phase_active = None
    try:
        # PHASE 1: STOP (RQ suspension and drain)
        if "stop" in maintenance_phases:
            enable_maintenance_mode(display)
            maintenance_enabled = True
            maintenance_phase_active = "stop"

        if suspension_needed:
            if not _suspend_rq_workers(
                display, wait_workers, wait_workers_timeout, wait_workers_poll, wait_workers_verbose
            ):
                raise typer.Exit(code=1)

        # TRANSITION: stop → migrate
        if maintenance_phase_active == "stop" and "migrate" not in maintenance_phases:
            disable_maintenance_mode(display)
            maintenance_enabled = False
            maintenance_phase_active = None

        # PHASE 2: MIGRATE (bench migrate)
        if "migrate" in maintenance_phases and not maintenance_enabled:
            enable_maintenance_mode(display)
            maintenance_enabled = True
            maintenance_phase_active = "migrate"

        if not _run_migration(display, migrate_timeout, migrate_command):
            _handle_migrate_failure(display, services_to_target, suspension_needed, wait)
            raise typer.Exit(code=1)

        # PHASE 3: START (service restart) - handled in success handler
        _handle_migrate_success(
            display, services_to_target, suspension_needed, wait, force_kill_timeout, maintenance_phases
        )

    finally:
        if maintenance_enabled:
            disable_maintenance_mode(display)


def _handle_migrate_success(
    display: DisplayManager,
    services_to_target: List[str],
    suspension_needed: bool,
    wait: bool,
    force_kill_timeout: Optional[int],
    maintenance_phases: set,
):
    """Handle migration success - full service restart.

    Success steps:
    1. Handle migrate→start phase transition for maintenance mode
    2. Restart ALL supervisor services (full parallel restart)
    3. Disable maintenance mode if enabled

    Full restart ensures all services pick up schema changes.
    Phase transition: If maintenance was ON from previous phases (stop/migrate)
    and "start" phase not requested, turn OFF before restart. If "start" phase
    requested, keep ON (or turn ON if not already).

    Note: RQ resume is handled by caller's finally block.
    """
    display.success("Migration succeeded. Restarting all services...")

    # Check if maintenance currently ON from previous phases (stop or migrate)
    maintenance_currently_on = "stop" in maintenance_phases or "migrate" in maintenance_phases

    # TRANSITION: migrate → start
    # If maintenance ON from previous phase but "start" not requested, turn OFF before restart
    if maintenance_currently_on and "start" not in maintenance_phases:
        disable_maintenance_mode(display)

    # PHASE 3: START
    # Enable maintenance for restart phase if requested and not already ON
    maintenance_enabled_for_restart = False
    if "start" in maintenance_phases:
        if not maintenance_currently_on:
            enable_maintenance_mode(display)
        maintenance_enabled_for_restart = True

    execute_parallel_command(
        services_to_target,
        util_restart_service,
        action_verb="restarting",
        show_progress=True,
        wait=wait,
        force_kill_timeout=force_kill_timeout,
    )

    if maintenance_enabled_for_restart:
        disable_maintenance_mode(display)


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

    Note: RQ resume is handled by caller's finally block.
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
            help="Run migration without stopping all services first (default: 'bench migrate --skip-failing-patches'). "
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
        Optional[bool],
        typer.Option(
            "--wait-workers/--no-wait-workers",
            help="Wait for RQ workers to become idle/suspended before restarting. Implies --suspend-rq.",
            show_default=False,
        ),
    ] = None,
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
    wait_workers_verbose: Annotated[
        bool,
        typer.Option(
            "--wait-workers-verbose",
            help="Show detailed worker states during --wait-workers checks.",
        ),
    ] = False,
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
            help="Custom migrate command to run (default: 'bench migrate --skip-failing-patches'). "
            "Example: 'bench migrate' or 'bench --site mysite.localhost migrate'.",
        ),
    ] = None,
    maintenance_mode: Annotated[
        Optional[List[str]],
        typer.Option(
            "--maintenance-mode",
            help="Enable maintenance mode for selected phases. Accepts a space separated list: stop migrate start.",
            show_default=False,
        ),
    ] = None,
):
    """
    Restart services with optional RQ worker coordination, migration, and maintenance mode.

    Performs supervisor-based restart with optional Redis worker suspension.
    Can run bench migrate between stop and start phases.
    Can wait for workers to complete jobs or signal them for graceful shutdown.
    Always attempts to resume workers after restart completion.

    Maintenance mode can be enabled for any combination of phases (stop, migrate, restart)
    using --maintenance-mode. Example: --maintenance-mode stop,migrate
    """
    display: DisplayManager = ctx.obj['display']

    valid_phases = {"stop", "migrate", "start"}
    maintenance_phases = set(maintenance_mode or [])
    if not maintenance_phases.issubset(valid_phases):
        display.error(
            f"Invalid value(s) for --maintenance-mode: {', '.join(maintenance_phases - valid_phases)}. "
            f"Allowed values: stop, migrate, start."
        )
        raise typer.Exit(code=1)

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    valid, target_desc = validate_services(display, services_to_target, all_services, "restart")
    if not valid:
        return

    wait_desc = "(with wait)" if wait else "(without wait)"
    display.print(f"\nAttempting to restart {target_desc} {wait_desc}...")

    suspension_needed = suspend_rq or (wait_workers is True)

    # Parse migrate_command string into list if provided
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
                wait_workers_verbose,
                migrate_timeout,
                migrate_command_list,
                wait,
                maintenance_phases,
                force_kill_timeout,
            )
        finally:
            if suspension_needed:
                _resume_rq_workers(display)
            display.print("\nRestart sequence complete.")
    else:
        maintenance_enabled = False
        try:
            # ---- STOP PHASE ----
            if "stop" in maintenance_phases:
                enable_maintenance_mode(display)
                maintenance_enabled = True

            if suspension_needed:
                if not _suspend_rq_workers(
                    display, wait_workers, wait_workers_timeout, wait_workers_poll, wait_workers_verbose
                ):
                    raise typer.Exit(code=1)

            if wait_workers is False:
                _signal_workers_for_graceful_shutdown(display, services_to_target)

            execute_parallel_command(
                services_to_target,
                util_stop_service,
                action_verb="stopping",
                show_progress=True,
                process_name_list=None,
                wait=wait,
                wait_workers=wait_workers,
                force_kill_timeout=force_kill_timeout,
            )

            # Disable maintenance mode if not needed for next phase
            if "stop" in maintenance_phases and "migrate" not in maintenance_phases:
                disable_maintenance_mode(display)
                maintenance_enabled = False

            # ---- MIGRATE PHASE ----
            if migrate:
                if "migrate" in maintenance_phases and not maintenance_enabled:
                    enable_maintenance_mode(display)
                    maintenance_enabled = True

                if not _run_migration(display, migrate_timeout):
                    display.error("Migration failed. Aborting restart - services remain stopped.")
                    raise typer.Exit(code=1)

                if "migrate" in maintenance_phases and "restart" not in maintenance_phases:
                    disable_maintenance_mode(display)
                    maintenance_enabled = False

            # ---- START PHASE ----
            if "start" in maintenance_phases and not maintenance_enabled:
                enable_maintenance_mode(display)
                maintenance_enabled = True

            execute_parallel_command(
                services_to_target,
                util_start_service,
                action_verb="starting",
                show_progress=True,
                process_name_list=None,
                wait=wait,
            )

        finally:
            if suspension_needed:
                _resume_rq_workers(display)
            if maintenance_enabled:
                disable_maintenance_mode(display)
            display.print("\nRestart sequence complete.")
