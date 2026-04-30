import os
import subprocess
import time
from typing import Annotated, Optional, List
import typer

try:
    from typer_examples import example as _example
except ImportError:

    def _example(*a, **kw):  # type: ignore[misc]
        def _noop(fn):
            return fn

        return _noop


from fmx.display import DisplayManager
from fmx.command_utils import validate_services, resolve_service_targets, format_wait_desc
from fmx.config import set_common_site_config_value
from fmx.cli import ServiceNameEnumFactory, execute_parallel_command, get_service_names_for_completion
from fmx.supervisor.api import (
    start_service as util_start_service,
    restart_service as util_restart_service,
)
from fmx.commands._rq_helpers import suspend_rq_workers, resume_rq_workers, run_with_optional_rq_drain


command_name = "restart"

ServiceNamesEnum = ServiceNameEnumFactory()


def set_maintenance_mode(display: DisplayManager, enabled: bool) -> None:
    """Enable or disable maintenance mode in common_site_config.json.

    Args:
        display: DisplayManager for output.
        enabled: True to enable maintenance mode, False to disable it.

    Raises:
        typer.Exit: With code 1 if writing the config fails while enabling.
            Failures while disabling are logged as errors but do not exit.
    """
    verb = "Enabling" if enabled else "Disabling"
    state = "enabled" if enabled else "disabled"
    value = 1 if enabled else 0
    display.warning(f"{verb} maintenance mode...")
    if set_common_site_config_value("maintenance_mode", value, display=display):
        display.success(f"Maintenance mode {state}.")
    else:
        display.error(f"Failed to {verb.lower()} maintenance mode in common_site_config.json.")
        if enabled:
            raise typer.Exit(code=1)


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
        last_was_progress = False
        last_progress_label = None
        try:
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    line = output.rstrip('\r\n')
                    if line:
                        is_progress_bar = '[' in line and ']' in line and '%' in line
                        if is_progress_bar:
                            stripped = line.strip()
                            label = stripped.split(' : [', 1)[0].strip() if ' : [' in stripped else 'Progress'
                            pct = (stripped.rsplit('%', 1)[0].rsplit(None, 1)[-1] + '%') if '%' in stripped else '?%'
                            if label != last_progress_label:
                                if last_was_progress:
                                    print()
                                print(f"  {label}  ", end='', flush=True)
                                last_progress_label = label
                            print(f"\r  {label}  {pct}   ", end='', flush=True)
                            last_was_progress = True
                        else:
                            if last_was_progress:
                                print()
                                last_was_progress = False
                                last_progress_label = None
                            display.print(f"  {line}")
                            output_lines.append(line)

                if time.time() - start_time > migrate_timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise subprocess.TimeoutExpired(["bench", "migrate"], migrate_timeout)

        finally:
            if last_was_progress:
                print()
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
    drain_workers: bool,
    drain_workers_timeout: int,
    drain_workers_poll: int,
    migrate_timeout: int,
    migrate_command: Optional[List[str]],
    wait: bool,
    maintenance_on_drain: bool = False,
    maintenance_on_migrate: bool = False,
    debug: bool = False,
    skip_stale: bool = True,
    stale_timeout: int = 15,
    worker_kill_timeout: int = 15,
    worker_kill_poll: float = 3.0,
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
            set_maintenance_mode(display, True)
            maintenance_enabled = True

        if suspension_needed:
            if not suspend_rq_workers(
                display,
                drain_workers,
                drain_workers_timeout,
                drain_workers_poll,
                debug,
                skip_stale=skip_stale,
                stale_timeout=stale_timeout,
            ):
                raise typer.Exit(code=1)

        # TRANSITION: drain → migrate
        # Drain is done; if migrate phase doesn't need maintenance, turn it off now
        if maintenance_enabled and not maintenance_on_migrate:
            set_maintenance_mode(display, False)
            maintenance_enabled = False

        # PHASE 2: MIGRATE (bench migrate)
        if maintenance_on_migrate and not maintenance_enabled:
            set_maintenance_mode(display, True)
            maintenance_enabled = True

        if not _run_migration(display, migrate_timeout, migrate_command):
            _handle_migrate_failure(display, services_to_target, suspension_needed, wait)
            if suspension_needed:
                resume_rq_workers(display)
            raise typer.Exit(code=1)

        # Resume workers BEFORE restarting services so they can start properly
        if suspension_needed:
            resume_rq_workers(display)

        # PHASE 3: SERVICE RESTART - handled in success handler
        maintenance_disabled_by_handler = _handle_migrate_success(
            display,
            services_to_target,
            wait,
            maintenance_on_migrate,
            drain_workers=suspension_needed,
            worker_kill_timeout=worker_kill_timeout,
            worker_kill_poll=worker_kill_poll,
        )
        if maintenance_disabled_by_handler:
            maintenance_enabled = False

    finally:
        if maintenance_enabled:
            set_maintenance_mode(display, False)


def _handle_migrate_success(
    display: DisplayManager,
    services_to_target: List[str],
    wait: bool,
    maintenance_on_migrate: bool = False,
    drain_workers: bool = False,
    worker_kill_timeout: int = 15,
    worker_kill_poll: float = 3.0,
) -> bool:
    """Handle migration success - full service restart.

    Restarts all supervisor services in parallel, then disables maintenance
    mode if it was enabled for the migrate phase.

    Args:
        display: DisplayManager for output
        services_to_target: List of supervisor service names to restart
        wait: Wait for supervisor restart operations to complete
        maintenance_on_migrate: Whether maintenance mode was enabled for the migrate phase
        drain_workers: Whether workers were drained before migration. When True, workers use
            normal stopProcess instead of the USR1 (warm shutdown) path, avoiding the 10s
            wait-for-current-job overhead on already-idle workers.

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
        wait_workers=drain_workers,
        worker_kill_timeout=worker_kill_timeout,
        worker_kill_poll=worker_kill_poll,
    )

    if maintenance_on_migrate:
        set_maintenance_mode(display, False)
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


@_example(
    "Restart with job draining (default)",
    "",
    detail=(
        "Workers stop picking up new jobs (a Redis suspend flag is set), then fmx polls until "
        "every worker is idle (up to 5 min). Only then are the services restarted. "
        "This is the default — use whenever workers may be executing jobs you cannot afford to "
        "lose, e.g. email sends, report generation, or file imports."
    ),
)
@_example(
    "Restart immediately — jobs are interrupted",
    "--no-drain-workers",
    detail=(
        "Skips worker draining. Workers are killed via SIGUSR1 (RQ's warm-shutdown signal). "
        "Any job currently executing is interrupted and will be marked failed or retried. "
        "Use after a code push that does not touch the DB schema and where losing an "
        "in-flight background job is acceptable."
    ),
)
@_example(
    "Run DB migration first, then restart",
    "--migrate",
    detail=(
        "Runs 'bench migrate --skip-failing' before restarting. Non-worker services (web, "
        "socketio) stay up during migration so the site remains accessible. Workers are drained "
        "by default — add --no-drain-workers only if the migration does not change tables that "
        "running jobs touch."
    ),
)
@_example(
    "Safest deploy: drain jobs, migrate DB, restart",
    "--drain-workers --migrate",
    detail=(
        "The recommended production deploy sequence: "
        "(1) suspend workers + wait for in-flight jobs to finish, "
        "(2) run bench migrate — non-workers (web, socketio) stay up throughout, "
        "(3) restart all services. "
        "Aborts cleanly if migration fails. "
        "Zero job loss, zero schema-vs-code mismatch window."
    ),
)
@_example(
    "Production deploy with a maintenance page",
    "--drain-workers --migrate --maintenance-mode drain --maintenance-mode migrate",
    detail=(
        "Same as the safest deploy above but also sets maintenance_mode=1 in "
        "common_site_config.json for each phase, so Frappe serves its built-in maintenance "
        "page to users. The mode is always cleared on completion or failure — even if "
        "something crashes mid-way."
    ),
)
@_example(
    "Restart only specific services",
    "{svc1} {svc2}",
    svc1="short-worker",
    svc2="long-worker",
    detail=(
        "Pass one or more service names to target a subset. Useful when only worker code "
        "changed and you want to leave web / socketio untouched. Service names: "
        "frappe, short-worker, long-worker, schedule, socketio."
    ),
)
@_example(
    "Increase kill wait for slow workers",
    "--worker-kill-timeout 30 --worker-kill-poll 2",
    detail=(
        "After SIGUSR1 fmx polls every --worker-kill-poll seconds and gives up after "
        "--worker-kill-timeout seconds, then force-kills via supervisord stopProcess. "
        "Increase the timeout when workers need extra time to finish their current loop "
        "before they can honour the shutdown signal. Only applies to the non-drain path."
    ),
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
    migrate: Annotated[
        bool,
        typer.Option(
            "--migrate",
            help="Run bench migrate before restarting. Non-worker services stay up during migration; "
            "on success all services restart, on failure any stopped services are started. "
            "Use with --drain-workers for zero-job-loss safety.",
        ),
    ] = False,
    migrate_timeout: Annotated[
        int,
        typer.Option(
            "--migrate-timeout",
            help="Timeout in seconds for bench migrate.",
        ),
    ] = 300,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for the final supervisor restart operations to complete before returning.",
        ),
    ] = True,
    drain_workers: Annotated[
        bool,
        typer.Option(
            "--drain-workers",
            help="Suspend RQ workers via a Redis flag so they stop picking up new jobs, "
            "then wait for any in-progress job to finish before restarting. Workers with no active "
            "job are skipped (see --skip-stale-workers). Enabled by default. "
            "Use --no-drain-workers to skip draining and force-kill workers via SIGUSR1 immediately.",
            is_flag=True,
        ),
    ] = True,
    drain_workers_timeout: Annotated[
        int,
        typer.Option(
            "--drain-workers-timeout",
            help="Timeout in seconds to wait for in-progress RQ jobs to finish after workers are suspended. "
            "If the timeout expires, the restart is aborted. Increase for long-running jobs.",
        ),
    ] = 300,
    drain_workers_poll: Annotated[
        int,
        typer.Option(
            "--drain-workers-poll",
            help="Polling interval in seconds when checking whether all RQ workers have become idle.",
        ),
    ] = 5,
    skip_stale_workers: Annotated[
        bool,
        typer.Option(
            "--skip-stale-workers/--no-skip-stale-workers",
            help="With --drain-workers, treat workers that have been idle for longer than --skip-stale-timeout "
            "as stale and skip waiting for them. Prevents a hung or crashed worker from blocking the restart.",
        ),
    ] = True,
    skip_stale_timeout: Annotated[
        int,
        typer.Option(
            "--skip-stale-timeout",
            help="Seconds of idleness after which a post-suspension worker is considered stale and skipped. "
            "Only used with --skip-stale-workers.",
        ),
    ] = 15,
    migrate_command: Annotated[
        Optional[str],
        typer.Option(
            "--migrate-command",
            help="Custom migrate command (default: 'bench migrate --skip-failing'). "
            "Example: 'bench --site mysite.localhost migrate'.",
        ),
    ] = None,
    maintenance_mode: Annotated[
        Optional[List[str]],
        typer.Option(
            "--maintenance-mode",
            help=(
                "Enable maintenance mode for specific phases: "
                "'drain' (during RQ worker drain) and/or 'migrate' (during bench migrate + service restart)."
            ),
            show_default=False,
        ),
    ] = None,
    worker_kill_timeout: Annotated[
        int,
        typer.Option(
            "--worker-kill-timeout",
            help="Timeout in seconds to wait for a worker process to exit after SIGUSR1 before falling back to stopProcess.",
        ),
    ] = 15,
    worker_kill_poll: Annotated[
        float,
        typer.Option(
            "--worker-kill-poll",
            help="Polling interval in seconds when waiting for a worker to exit after SIGUSR1.",
        ),
    ] = 3.0,
):
    """Restart services or specific processes."""
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

    suspension_needed = drain_workers

    if maintenance_on_drain and not suspension_needed:
        display.warning(
            "--maintenance-mode drain has no effect without --drain-workers. "
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
    services_to_target = resolve_service_targets(service_names, all_services)

    valid, target_desc = validate_services(display, services_to_target, all_services, "restart")
    if not valid:
        return

    wait_desc = format_wait_desc(wait)
    display.print(f"\nRestarting {target_desc} {wait_desc}...")

    migrate_command_list: Optional[List[str]] = None
    if migrate_command:
        migrate_command_list = migrate_command.split()

    if migrate:
        if not suspension_needed:
            display.warning(
                "Running migration without suspending RQ workers. "
                "Active jobs may fail if migration changes the database schema. "
                "Consider using --drain-workers for safe migration."
            )

        try:
            _run_migrate_flow(
                display,
                services_to_target,
                suspension_needed,
                drain_workers,
                drain_workers_timeout,
                drain_workers_poll,
                migrate_timeout,
                migrate_command_list,
                wait,
                maintenance_on_drain=maintenance_on_drain,
                maintenance_on_migrate=maintenance_on_migrate,
                debug=debug,
                skip_stale=skip_stale_workers,
                stale_timeout=skip_stale_timeout,
                worker_kill_timeout=worker_kill_timeout,
                worker_kill_poll=worker_kill_poll,
            )
        finally:
            if suspension_needed:
                resume_rq_workers(display)
    else:
        maintenance_enabled = False

        def _do_restart():
            nonlocal maintenance_enabled
            if maintenance_enabled:
                set_maintenance_mode(display, False)
                maintenance_enabled = False

            execute_parallel_command(
                services_to_target,
                util_restart_service,
                action_verb="restarting",
                show_progress=True,
                wait=wait,
                wait_workers=drain_workers,
                worker_kill_timeout=worker_kill_timeout,
                worker_kill_poll=worker_kill_poll,
            )

        try:
            if maintenance_on_drain and suspension_needed:
                set_maintenance_mode(display, True)
                maintenance_enabled = True

            run_with_optional_rq_drain(
                display=display,
                drain_workers=suspension_needed,
                drain_workers_timeout=drain_workers_timeout,
                drain_workers_poll=drain_workers_poll,
                debug=debug,
                skip_stale=skip_stale_workers,
                stale_timeout=skip_stale_timeout,
                action_fn=_do_restart,
            )
        finally:
            if maintenance_enabled:
                set_maintenance_mode(display, False)
