import sys
import signal
import os
from typing import Annotated, Optional, List, Tuple, Any
from ..supervisor.executor import execute_supervisor_command


import typer
from rich import print

# Use relative imports within the package
try:
    from ..cli import (
        ServiceNameEnumFactory,
        _run_wait_jobs,
        execute_parallel_command,
        get_service_names_for_completion,
        _cached_service_names,
    )
    from ..supervisor.api import (
        restart_service as util_restart_service,
        signal_service as util_signal_service,
        get_service_info as util_get_service_info,
    )
    from ..supervisor.connection import FM_SUPERVISOR_SOCKETS_DIR
    from ..supervisor.constants import is_worker_process, ProcessStates
    from ..supervisor.exceptions import SupervisorError, SupervisorConnectionError
    # Import necessary components for config key management
    import json
    import contextlib
    from pathlib import Path
    from ..workers import _update_site_config_key, _get_site_config_key_value
except ImportError as e:
    print(f"[bold red]Error:[/bold red] Failed to import required modules: {e}")
    print("Ensure fm-helper structure is correct and dependencies are installed.")
    sys.exit(1)


command_name = "graceful-restart"

ServiceNamesEnum = ServiceNameEnumFactory()

# --- Helper to get worker process names for a service ---
def _get_worker_processes_for_service(service_name: str) -> List[str]:
    """Fetches process info and returns names of worker processes."""
    worker_names = []
    try:
        # Use execute_parallel_command with a placeholder action 'INFO'
        process_info_results = execute_parallel_command(
            [service_name],
            lambda s, **kw: execute_supervisor_command(s, action="info"),
            action_verb="getting info for",
            show_progress=False,
            action='INFO'
        )

        info_list = process_info_results.get(service_name) if isinstance(process_info_results, dict) else None

        if isinstance(info_list, list):
             for proc_info in info_list:
                 if 'name' in proc_info and is_worker_process(proc_info['name']):
                     worker_names.append(proc_info['name'])
        elif info_list is None and process_info_results is not None and service_name in process_info_results:
             print(f"[yellow]Warning:[/yellow] Could not retrieve process info for {service_name} to identify workers (result was None).")
        elif info_list is None:
             pass # Error already logged

    except SupervisorConnectionError:
         print(f"[yellow]Warning:[/yellow] Could not connect to {service_name} to identify workers.")
    except Exception as e:
        print(f"[red]Error identifying workers for {service_name}: {e}[/red]")
        import traceback
        traceback.print_exc()

    return worker_names


# --- Command Definition ---
def command(
    ctx: typer.Context,
    service_names: Annotated[
        Optional[List[ServiceNamesEnum]],
        typer.Argument(
            help="Name(s) of the service(s) to target. If omitted, targets ALL running services.",
            autocompletion=get_service_names_for_completion,
            show_default=False,
        )
    ] = None,
    wait_jobs: Annotated[
        bool,
        typer.Option(
            "--wait-jobs",
            help="Wait for active Frappe background jobs ('started' state) to finish after suspending workers.",
        )
    ] = False,
    site_name: Annotated[
        Optional[str],
        typer.Option(
            "--site-name",
            help="Frappe site name (required if --wait-jobs, --pause-scheduler, or --maintenance-mode is used).",
        )
    ] = None,
    wait_jobs_timeout: Annotated[
        int,
        typer.Option(
            "--wait-jobs-timeout",
            help="Timeout (seconds) for waiting for jobs (default: 300).",
        )
    ] = 300,
    wait_jobs_poll: Annotated[
        int,
        typer.Option(
            "--wait-jobs-poll",
            help="Polling interval (seconds) for checking jobs (default: 5).",
        )
    ] = 5,
    wait_jobs_queue: Annotated[
        Optional[List[str]],
        typer.Option(
            "--queue", "-q",
            help="Specific job queue(s) to monitor when using --wait-jobs. Use multiple times (e.g., -q short -q long). Monitors all if not specified.",
        )
    ] = None,
    pause_scheduler: Annotated[
        bool,
        typer.Option(
            "--pause-scheduler",
            help="Pause the Frappe scheduler before waiting for jobs and unpause after. Requires --wait-jobs and --site-name.",
        )
    ] = False,
    maintenance_mode: Annotated[
        bool,
        typer.Option(
            "--maintenance-mode",
            help="Set 'maintenance_mode: true' in site_config.json before waiting for jobs and restore original state after waiting. Requires --wait-jobs and --site-name.",
        )
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for the final supervisor restart operations to complete before returning.",
        )
    ] = True,
):
    """
    Gracefully restart services using RQ worker suspension (SIGUSR2).

    Workflow:
    1. Sends SIGUSR2 to worker processes, causing them to finish current jobs and suspend.
    2. (Optional) Waits for Frappe background jobs to complete (--wait-jobs).
    3. Restarts all target services using standard supervisor restart.
    """
    if not _cached_service_names:
        print(f"[bold red]Error:[/bold red] No supervisord services found.", file=sys.stderr)
        print(f"Looked for socket files in: {FM_SUPERVISOR_SOCKETS_DIR}", file=sys.stderr)
        print("Ensure Frappe Manager services are running.", file=sys.stderr)
        raise typer.Exit(code=1)

    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else [s.value for s in service_names]

    invalid_services = [s for s in services_to_target if s not in all_services]
    if invalid_services:
        print(f"[red]Error:[/red] Invalid service name(s): {', '.join(invalid_services)}")
        print(f"Available services: {', '.join(all_services) or 'None'}")
        raise typer.Exit(code=1)

    target_desc = "all services" if not service_names else f"service(s): [b cyan]{', '.join(services_to_target)}[/b cyan]"
    print(f"Attempting Graceful Restart (Suspend + Wait + Restart) for {target_desc}...")

    # Variables to store state returned from job waiting / set by this script
    original_scheduler_state: Optional[int] = None
    scheduler_was_paused: bool = False
    original_maintenance_state: Optional[Any] = None
    maintenance_was_managed_by_script: bool = False
    site_config_path: Optional[Path] = None

    # --- Validations ---
    if (pause_scheduler or maintenance_mode or wait_jobs) and not site_name:
        print("[red]Error:[/red] --site-name is required when using --wait-jobs, --pause-scheduler, or --maintenance-mode.")
        raise typer.Exit(code=1)

    if site_name:
        site_config_path = Path(f"/workspace/frappe-bench/sites/{site_name}/site_config.json")

    # --- Step 1: Suspend Workers (SIGUSR2) ---
    print(f"\n[STEP 1/3] Suspending worker processes (SIGUSR2) in {target_desc}...")
    all_workers_to_suspend: Dict[str, List[str]] = {}
    workers_found = False
    for service in services_to_target:
        workers = _get_worker_processes_for_service(service)
        if workers:
            all_workers_to_suspend[service] = workers
            workers_found = True
            print(f"  Identified workers in [magenta]{service}[/magenta]: {', '.join(workers)}")
        else:
            print(f"  No worker processes identified in [magenta]{service}[/magenta].")

    if not workers_found:
        print("[yellow]Warning:[/yellow] No worker processes found in any target service. Skipping suspension step.")
    else:
        suspend_results = {}
        print("  Sending SIGUSR2...")
        for service, workers in all_workers_to_suspend.items():
            try:
                result = util_signal_service(service, "USR2", process_name_list=workers)
                suspend_results[service] = result
                if result:
                    print(f"  [green]Successfully sent SIGUSR2 to workers in {service}.[/green]")
                else:
                    print(f"  [yellow]Warning:[/yellow] Failed to send SIGUSR2 to some/all workers in {service}.")
            except Exception as e:
                 print(f"  [red]Error sending SIGUSR2 to {service}: {e}[/red]")
                 suspend_results[service] = False

        if not all(suspend_results.values()):
            print("[yellow]Warning:[/yellow] Failed to suspend workers in one or more services. Continuing, but restart might not be fully graceful.")
        else:
            print("[green]Worker suspension signals sent successfully.[/green]")

    # --- Step 2: Wait for Jobs (Optional) ---
    print(f"\n[STEP 2/3] Waiting for jobs (if requested)...")
    job_wait_performed = False
    if wait_jobs:
        job_wait_performed = True

        if maintenance_mode:
            print(f"\n[cyan]Checking and setting 'maintenance_mode' key for site: {site_name}...[/cyan]")
            try:
                original_maintenance_state = _get_site_config_key_value(
                    site_config_path, "maintenance_mode", default=None, verbose=True
                )
                if original_maintenance_state is not True:
                    _update_site_config_key(site_config_path, "maintenance_mode", True, verbose=True)
                    maintenance_was_managed_by_script = True
                    print(f"[green]'maintenance_mode' key set to true for site: {site_name}.[/green]")
                else:
                    print("[dim]'maintenance_mode' key already set to true. No action taken.[/dim]")
            except Exception as e:
                print(f"[bold red]Error:[/bold red] Failed to check or set 'maintenance_mode' key for site {site_name}: {e}")
                print("Aborting graceful restart.")
                if maintenance_was_managed_by_script:
                    try:
                        _update_site_config_key(site_config_path, "maintenance_mode", original_maintenance_state, verbose=False)
                        print(f"[yellow]Attempted to restore 'maintenance_mode' key before exiting.[/yellow]")
                    except: pass
                raise typer.Exit(code=1)

        pause_desc = " and pausing scheduler" if pause_scheduler else ""
        maint_desc = " (after setting maintenance mode key)" if maintenance_was_managed_by_script else ""
        print(f"[cyan]Waiting for active jobs{pause_desc}{maint_desc}...[/cyan]")
        print("-" * 30)
        wait_success, original_scheduler_state, scheduler_was_paused = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue,
            pause_scheduler_during_wait=pause_scheduler
        )
        print("-" * 30)

        if maintenance_was_managed_by_script:
            print(f"\n[cyan]Restoring original 'maintenance_mode' key value ({json.dumps(original_maintenance_state)}) for site '{site_name}' (before restart)...[/cyan]", file=sys.stderr)
            try:
                _update_site_config_key(site_config_path, "maintenance_mode", original_maintenance_state, verbose=True)
                print(f"[green]'maintenance_mode' key restored for site '{site_name}'.[/green]", file=sys.stderr)
            except Exception as e:
                print(f"\n[bold yellow]Warning:[/bold yellow] Failed to restore 'maintenance_mode' key in {site_config_path}: {e}", file=sys.stderr)
                print(f"[yellow]Please manually ensure 'maintenance_mode' is set correctly (should be {json.dumps(original_maintenance_state)}) in the file.[/yellow]", file=sys.stderr)
            maintenance_was_managed_by_script = False

        if not wait_success:
            print("[bold red]Error:[/bold red] Job waiting failed or timed out.")
            print("Aborting graceful restart process. Services were suspended but not restarted.")
            if scheduler_was_paused:
                 try:
                     _update_site_config_key(site_config_path, "pause_scheduler", original_scheduler_state, verbose=False)
                     print(f"[yellow]Attempted to restore scheduler state before exiting.[/yellow]")
                 except: pass
            raise typer.Exit(code=1)

        print("[green]Job waiting successful.[/green]")
    else:
        print("  --wait-jobs not specified, skipping job wait.")

    # --- Step 3: Restart Services ---
    print(f"\n[STEP 3/3] Restarting {target_desc}...")
    try:
        execute_parallel_command(
            services_to_target,
            util_restart_service,
            action_verb="restarting",
            show_progress=True,
            wait=wait,
            wait_workers=True,
            force_kill_timeout=None,
        )

    finally:
        if scheduler_was_paused:
            print(f"\n[cyan]Restoring original scheduler state ({original_scheduler_state}) for site '{site_name}'...[/cyan]", file=sys.stderr)
            try:
                _update_site_config_key(site_config_path, "pause_scheduler", original_scheduler_state, verbose=True)
                print(f"[green]Scheduler state restored for site '{site_name}'.[/green]", file=sys.stderr)
            except Exception as e:
                print(f"\n[bold yellow]Warning:[/bold yellow] Failed to automatically restore 'pause_scheduler' key in {site_config_path}: {e}", file=sys.stderr)
                print(f"[yellow]Please manually ensure 'pause_scheduler' is set correctly (should be {json.dumps(original_scheduler_state)}) in the file.[/yellow]", file=sys.stderr)

        if maintenance_was_managed_by_script:
            print(f"\n[cyan]Attempting final restoration of 'maintenance_mode' key ({json.dumps(original_maintenance_state)}) for site '{site_name}'...[/cyan]", file=sys.stderr)
            try:
                _update_site_config_key(site_config_path, "maintenance_mode", original_maintenance_state, verbose=True)
                print(f"[green]'maintenance_mode' key restored (final check).[/green]", file=sys.stderr)
            except Exception as e:
                print(f"\n[bold yellow]Warning:[/bold yellow] Failed final restoration of 'maintenance_mode' key in {site_config_path}: {e}", file=sys.stderr)

        print(f"\nGraceful restart process complete for {target_desc}.")
