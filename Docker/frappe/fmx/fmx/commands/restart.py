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
    signal_service as util_signal_service,
)
from fmx.commands._rq_helpers import suspend_rq_workers, resume_rq_workers, run_with_optional_rq_drain


command_name = "restart"

# Signal used by the --graceful path. SIGHUP triggers an in-place worker reload in
# gunicorn (the master never closes its listening socket, so upstream proxies do not
# observe a connection-refused window across the reload).
GRACEFUL_RELOAD_SIGNAL = "HUP"

ServiceNamesEnum = ServiceNameEnumFactory()


def _graceful_reload_service(
    service_name: str,
    progress_callback=None,
    **_extra_kwargs,
):
    """Signal-based in-place reload of every process in a service.

    Designed to plug into ``execute_parallel_command`` in the same slot as
    ``util_restart_service``. Accepts (and ignores) the restart-specific kwargs
    (``wait``, ``wait_workers``, ``worker_kill_timeout``, ``worker_kill_poll``,
    ``verbose``) so the caller signature is interchangeable. Discovers the current
    process list for the service and dispatches ``GRACEFUL_RELOAD_SIGNAL`` to each
    one via the supervisor XML-RPC API.

    For the frappe web service this hands SIGHUP to the gunicorn master, which forks
    fresh workers using the still-open listening socket and only then retires the old
    workers — eliminating the brief connect-refused window of a stop/start cycle.
    """
    # Imported here to avoid a circular import at module load time.
    from fmx.supervisor.connection import check_supervisord_connection

    try:
        conn = check_supervisord_connection(service_name)
        all_info = conn.supervisor.getAllProcessInfo() or []
    except Exception as e:
        return {"signalled": [], "failed": [service_name], "error": str(e)}

    process_names = [info["name"] for info in all_info]
    if not process_names:
        return {"signalled": [], "failed": []}

    ok = util_signal_service(service_name, GRACEFUL_RELOAD_SIGNAL, process_name_list=process_names)

    if progress_callback:
        for name in process_names:
            info = next((i for i in all_info if i.get("name") == name), {})
            pid = info.get("pid") or 0
            progress_callback(
                service_name,
                name,
                "reload",
                pid,
                f"signal {GRACEFUL_RELOAD_SIGNAL}" if ok else "failed",
                0.0,
            )

    if ok:
        return {"signalled": process_names, "failed": []}
    return {"signalled": [], "failed": process_names}


def _print_graceful_reload_summary(display: DisplayManager, results: dict, elapsed: float) -> None:
    """Print a success/failure summary for the ``--graceful`` reload path.

    ``execute_parallel_command`` only formats summaries for the built-in
    restart/start/stop handlers, so the graceful path has to surface its own
    outcome. Mirrors the shape of ``_handle_restart_results`` in ``fmx.cli``.
    """
    total_services = len(results)
    failed_services: list[str] = []
    total_count = 0

    for svc, result in results.items():
        if isinstance(result, dict) and not result.get("error"):
            total_count += len(result.get("signalled", []))
            if result.get("failed"):
                failed_services.append(svc)
        else:
            failed_services.append(svc)

    elapsed_str = f"  [dim]({elapsed:.1f}s)[/dim]"

    if not failed_services:
        display.print(
            f"\n[green]✔[/green]  Reloaded [bold]{total_services}[/bold] service(s) · "
            f"[bold]{total_count}[/bold] process(es){elapsed_str}"
        )
    else:
        ok = total_services - len(failed_services)
        display.print(
            f"\n[yellow]⚠[/yellow]  Reloaded [bold]{ok}/{total_services}[/bold] service(s){elapsed_str}"
            f"  —  [red]{', '.join(failed_services)}[/red] failed"
        )


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
    """Run bench migrate with optional timeout and real-time output.

    Logic:
    1. Executes 'bench migrate' (or custom command) from /workspace/frappe-bench directory
    2. Shows real-time output during migration
    3. Applies specified timeout to prevent hanging (0 = infinite)
    4. Returns success/failure status

    Args:
        display: DisplayManager for output
        migrate_timeout: Timeout in seconds (0 = wait indefinitely)
        migrate_command: Custom migrate command (default: ["bench", "migrate"])

    Returns:
        True if migration succeeded, False to abort restart
    """
    if migrate_command is None:
        migrate_command = ["bench", "migrate"]

    full_command = ' '.join(migrate_command)
    display.print(f"🔄 Running: cd /workspace/frappe-bench && {full_command}")
    if migrate_timeout > 0:
        display.dimmed(f"Migration timeout: {migrate_timeout}s")
    else:
        display.dimmed("Migration timeout: none (waiting indefinitely)")

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

                if migrate_timeout > 0 and time.time() - start_time > migrate_timeout:
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
        "every worker is idle. No timeout by default — waits indefinitely for jobs to finish. "
        "This is the safest default for production use. Use whenever workers may be executing jobs "
        "you cannot afford to lose, e.g. email sends, report generation, or file imports."
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
    "Set a drain timeout to abort stuck workers",
    "--drain-workers-timeout 600",
    detail=(
        "Wait up to 10 minutes for workers to finish, then abort the restart if any are still busy. "
        "Useful in automated deployments where you want a bounded wait time rather than the default "
        "infinite wait. The restart is aborted (not forced) if the timeout expires — no jobs are killed."
    ),
)
@_example(
    "Run DB migration first, then restart",
    "--migrate",
    detail=(
        "Runs 'bench migrate' before restarting. Non-worker services (web, "
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
@_example(
    "Graceful in-place reload (no upstream connection-refused window)",
    "frappe --graceful",
    detail=(
        "Signals the gunicorn master with SIGHUP so it forks fresh workers using the "
        "still-open listening socket and then retires the old workers. The master never "
        "closes the listener, so an upstream proxy observes no connect-refused window. "
        "Recommended for production deploys of the web service. Pair with --no-drain-workers "
        "(or just target only `frappe`) if you do not need to drain RQ workers."
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
            help="Timeout in seconds for bench migrate. Set to 0 (default) to wait indefinitely.",
        ),
    ] = 0,
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
            "--drain-workers/--no-drain-workers",
            help="Suspend RQ workers via a Redis flag so they stop picking up new jobs, "
            "then wait for any in-progress job to finish before restarting. Workers with no active "
            "job are skipped (see --skip-stale-workers). Enabled by default. "
            "Use --no-drain-workers to skip draining and force-kill workers via SIGUSR1 immediately.",
        ),
    ] = True,
    drain_workers_timeout: Annotated[
        int,
        typer.Option(
            "--drain-workers-timeout",
            help="Timeout in seconds to wait for in-progress RQ jobs to finish after workers are suspended. "
            "Set to 0 (default) to wait indefinitely. Set a specific value (e.g., 300, 600) if you want "
            "the restart to abort after that many seconds.",
        ),
    ] = 0,
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
            help="Custom migrate command (default: 'bench migrate'). "
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
    graceful: Annotated[
        bool,
        typer.Option(
            "--graceful",
            help=(
                "In-place reload: signal each targeted process with SIGHUP via supervisord "
                "instead of stop/start. Gunicorn handles SIGHUP by forking new workers using "
                "the still-open listening socket and then retiring the old workers — the "
                "master never closes the listener, so upstream proxies observe no "
                "connection-refused window. Recommended for production deploys of the web "
                "service. Mutually exclusive with --migrate (which performs a real restart)."
            ),
        ),
    ] = False,
):
    """Restart services or specific processes."""
    display: DisplayManager = ctx.obj['display']
    debug: bool = ctx.obj.get('debug', False)

    if graceful and migrate:
        display.error("--graceful and --migrate are mutually exclusive.")
        raise typer.Exit(code=1)

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

            if graceful:
                _reload_start = time.time()
                reload_results = execute_parallel_command(
                    services_to_target,
                    _graceful_reload_service,
                    action_verb="reloading",
                    show_progress=True,
                    wait=wait,
                    return_raw_results=True,
                )
                _print_graceful_reload_summary(
                    display, reload_results or {}, elapsed=time.time() - _reload_start,
                )
                return

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
