import os
import sys
import json
import subprocess
from enum import Enum
from typing import Annotated, Optional, List

import typer
from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live
from rich.panel import Panel

# Use relative imports within the package
try:
    from .supervisor_utils import (
        get_service_names as util_get_service_names,
        stop_service as util_stop_service,
        start_service as util_start_service, # Added start
        restart_service as util_restart_service,
        get_service_info as util_get_service_info,
        FM_SUPERVISOR_SOCKETS_DIR, # Import for error messages
    )
except ImportError:
    # Handle case where supervisor_utils failed (e.g., supervisor not installed)
    print("[bold red]Error:[/bold red] Failed to import supervisor utilities. Is 'supervisor' installed?")
    sys.exit(1)

# --- CLI Specific Helpers ---

# Store service names globally after first fetch to avoid redundant calls
_cached_service_names: Optional[List[str]] = None

def get_service_names_for_completion() -> List[str]:
    """Wrapper for Typer autocompletion, uses cached names."""
    global _cached_service_names
    if _cached_service_names is None:
        _cached_service_names = util_get_service_names()
    return _cached_service_names

def get_dynamic_service_name_enum():
    """Dynamically create an Enum for service names for Typer choices."""
    service_names = get_service_names_for_completion() # Use cached names
    if not service_names:
        # Return a dummy enum if no services are found to avoid Typer errors
        # Make the value descriptive but distinct
        return Enum("ServiceNames", {"_no_services_found_": "No services running or found"})
    return Enum("ServiceNames", {name: name for name in service_names})

# Create the Enum dynamically at import time
# This needs to be callable by Typer later
ServiceNameEnumFactory = get_dynamic_service_name_enum

# --- Parallel Execution ---

def execute_parallel_command(
    services: List[str],
    command_func,
    action_verb: str,
    show_progress: bool = True,
    **kwargs
):
    """Execute a command in parallel across multiple services with progress display."""
    if not services:
        print("[yellow]No services specified or found to execute command on.[/yellow]")
        return

    # Adjust max_workers: ensure at least 1, max os.cpu_count(), but no more than num services
    max_workers = min(max(1, os.cpu_count() or 1), len(services))

    results = {}
    futures = {}

    progress_context = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True, # Hide progress bar when done
        ) if show_progress else None

    try:
        with (progress_context or open(os.devnull, 'w')) as progress: # Use dummy context if no progress
            if progress:
                task_id = progress.add_task(f"{action_verb.capitalize()} services...", total=len(services))

            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fm_helper_worker") as executor:
                # Submit tasks
                for service in services:
                    future = executor.submit(command_func, service, **kwargs)
                    futures[future] = service

                # Process completed tasks as they finish
                for future in as_completed(futures):
                    service = futures[future]
                    try:
                        result = future.result()
                        results[service] = result # Store result (e.g., Tree for status, bool for stop/restart)
                    except Exception as e:
                        # Log error immediately
                        print(f"[bold red]Error {action_verb} {service}:[/bold red] {e}")
                        results[service] = None # Indicate failure
                    finally:
                         if progress:
                            progress.update(task_id, advance=1)

    except Exception as e:
         print(f"[bold red]An unexpected error occurred during parallel execution:[/bold red] {e}")
    finally:
        # Ensure progress bar stops even if interrupted
        # This might not be strictly necessary with transient=True and context manager
        pass


    # --- Process and Print Results ---

    # For status command, print the Trees
    if command_func == util_get_service_info:
        print("-" * 30)
        # Sort results by service name for consistent output
        for service in sorted(results.keys()):
            result = results[service]
            if result is not None:
                print(result) # Print the Rich Tree
                print() # Add spacing between service outputs
            # else: # Error already printed during execution
        print("-" * 30)

    # For stop/start/restart, summarize success/failure
    elif command_func in [util_stop_service, util_start_service, util_restart_service]:
        success_count = sum(1 for res in results.values() if res is True)
        fail_count = len(services) - success_count

        if fail_count == 0:
            print(f"[green]Successfully {action_verb} {success_count} service(s).[/green]")
        elif success_count == 0:
             print(f"[red]Failed to {action_verb} {fail_count} service(s).[/red]")
        else:
             print(f"[yellow]Finished {action_verb}: {success_count} succeeded, {fail_count} failed.[/yellow]")


# --- Typer App Definition ---

app = typer.Typer(
    name="fm-helper",
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="""
    [bold]fm-helper[/bold]: Interact with supervisord instances managed by Frappe Manager.

    Provides commands to [red]stop[/red], [green]start[/green], [blue]restart[/blue], and check the [yellow]status[/yellow]
    of background services (like Frappe, Workers, Scheduler) running within
    the Frappe Manager Docker environment.
    """,
    epilog=f"""
    Uses supervisord socket files typically located in: {FM_SUPERVISOR_SOCKETS_DIR}
    (controlled by the SUPERVISOR_SOCKET_DIR environment variable).
    """
)

# --- Common Arguments & Options ---

ServiceNameArgument = typer.Argument(
    None, # Default to None, meaning "all services" if not provided
    help="Name(s) of the service(s) to target. If omitted, targets ALL running services.",
    autocompletion=get_service_names_for_completion,
    show_default=False, # Don't show default=None
)

ProcessNameOption = typer.Option(
    None, # Default to None, meaning "all processes"
    "--process",
    "-p",
    help="Target only specific process(es) within the selected service(s). Use multiple times for multiple processes (e.g., -p worker_short -p worker_long).",
    show_default=False,
)

WaitOption = typer.Option(
    True, # Default to waiting
    "--wait/--no-wait",
    help="Wait for supervisor start/stop operations to complete before returning.",
)

# Options specifically for --wait-jobs
WaitJobsOption = typer.Option(
    False,
    "--wait-jobs",
    help="Wait for active Frappe background jobs ('started' state) to finish before completing stop/restart.",
)
SiteNameOption = typer.Option(
    None,
    "--site-name",
    help="Frappe site name (required if --wait-jobs is used).",
)
WaitJobsTimeoutOption = typer.Option(
    300,
    "--wait-jobs-timeout",
    help="Timeout (seconds) for waiting for jobs (default: 300).",
)
WaitJobsPollOption = typer.Option(
    5,
    "--wait-jobs-poll",
    help="Polling interval (seconds) for checking jobs (default: 5).",
)
WaitJobsQueueOption = typer.Option(
    None,
    "--queue", "-q",
    help="Specific job queue(s) to monitor when using --wait-jobs. Use multiple times (e.g., -q short -q long). Monitors all if not specified.",
)

# --- Helper for Job Waiting ---

def _run_wait_jobs(
    site_name: str,
    timeout: int,
    poll_interval: int,
    queues: Optional[List[str]]
) -> bool:
    """Runs the fm-wait-jobs script and handles its output."""
    command = [
        "fm-wait-jobs",
        site_name,
        "--timeout", str(timeout),
        "--poll-interval", str(poll_interval),
    ]
    if queues:
        for q in queues:
            command.extend(["--queue", q])

    print(f"\n[cyan]Waiting for active jobs on site '{site_name}' (timeout: {timeout}s)...[/cyan]")

    # Use subprocess.Popen to stream stderr for progress
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1, # Line buffered
    )

    stderr_lines = []
    # Display stderr lines live using Rich Live and Panel
    with Live(auto_refresh=False, transient=True) as live:
        if process.stderr:
            for line in process.stderr:
                cleaned_line = line.strip()
                if cleaned_line: # Avoid adding empty lines
                    stderr_lines.append(cleaned_line)
                    # Display the last line of stderr within a panel
                    live.update(Panel(f"[dim]{cleaned_line}[/dim]", border_style="dim", title="Job Status"), refresh=True)

    stdout, _ = process.communicate() # Get final stdout after process finishes
    returncode = process.returncode

    try:
        # Try parsing the final stdout as JSON
        result_json = json.loads(stdout.strip())
        status = result_json.get("status", "error")
        message = result_json.get("message", "No message from script.")
        remaining = result_json.get("remaining_jobs", -1)
    except json.JSONDecodeError:
        status = "error"
        message = f"Failed to parse JSON output from fm-wait-jobs script. Output: '{stdout.strip()}'"
        remaining = -1

    # Determine success/failure based on exit code and parsed status
    if returncode == 0 and status == "success":
        print(f"[green]✔ Success:[/green] {message}")
        return True
    elif returncode == 1 and status == "timeout":
        print(f"[yellow]⚠ Timeout:[/yellow] {message}. {remaining} job(s) might still be running.")
        return False # Indicate timeout/failure
    else:
        print(f"[red]✘ Error waiting for jobs (Exit Code: {returncode}):[/red] {message}")
        # Print captured stderr for debugging if there was an error (and not just timeout)
        if stderr_lines and returncode != 1:
             print("[bold dim]fm-wait-jobs stderr log:[/bold dim]")
             for line in stderr_lines:
                 print(f"[dim]- {line}[/dim]")
        return False # Indicate error


# --- Typer Commands ---

@app.command()
def stop(
    service_names: Annotated[Optional[List[str]], ServiceNameArgument],
    process_name: Annotated[Optional[List[str]], ProcessNameOption],
    wait: Annotated[bool, WaitOption],
    # Job waiting options
    wait_jobs: Annotated[bool, WaitJobsOption],
    site_name: Annotated[Optional[str], SiteNameOption],
    wait_jobs_timeout: Annotated[int, WaitJobsTimeoutOption],
    wait_jobs_poll: Annotated[int, WaitJobsPollOption],
    wait_jobs_queue: Annotated[Optional[List[str]], WaitJobsQueueOption],
):
    """
    🛑 [red]Stop[/red] managed services or specific processes within them.

    Can optionally wait for active background jobs to finish first using --wait-jobs.
    """
    # Resolve target services - use cache
    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else service_names

    # Validate provided service names
    invalid_services = [s for s in services_to_target if s not in all_services and s != "_no_services_found_"]
    if invalid_services:
        print(f"[red]Error:[/red] Invalid service name(s): {', '.join(invalid_services)}")
        print(f"Available services: {', '.join(all_services) or 'None'}")
        raise typer.Exit(code=1)

    target_desc = "all services" if not service_names else f"service(s): [b cyan]{', '.join(services_to_target)}[/b cyan]"
    process_desc = f"process(es): [b yellow]{', '.join(process_name)}[/b yellow]" if process_name else "all processes"

    print(f"Attempting to stop {process_desc} in {target_desc}...")
    execute_parallel_command(
        services_to_target,
        util_stop_service,
        action_verb="stopping",
        show_progress=True, # Show progress for supervisor stop
        process_name_list=process_name,
        wait=wait
    )

    # --- Wait for Jobs Logic ---
    if wait_jobs:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs.")
            raise typer.Exit(code=1)
        if process_name:
             print("[yellow]Warning:[/yellow] --wait-jobs checks all jobs for the site, ignoring specific --process flags for the wait.")

        # We proceed to wait for jobs regardless of supervisor stop success,
        # as workers might need to finish even if supervisor interaction failed.
        print("-" * 30)
        wait_success = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue
        )
        print("-" * 30)
        if not wait_success:
             print("[yellow]Continuing stop process despite job wait failure/timeout.[/yellow]")
        # else: # Success message printed by _run_wait_jobs


@app.command()
def start(
    service_names: Annotated[Optional[List[str]], ServiceNameArgument],
    process_name: Annotated[Optional[List[str]], ProcessNameOption],
    wait: Annotated[bool, WaitOption],
):
    """
    🟢 [green]Start[/green] managed services or specific processes within them.
    """
    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else service_names

    invalid_services = [s for s in services_to_target if s not in all_services and s != "_no_services_found_"]
    if invalid_services:
        print(f"[red]Error:[/red] Invalid service name(s): {', '.join(invalid_services)}")
        print(f"Available services: {', '.join(all_services) or 'None'}")
        raise typer.Exit(code=1)

    target_desc = "all services" if not service_names else f"service(s): [b cyan]{', '.join(services_to_target)}[/b cyan]"
    process_desc = f"process(es): [b yellow]{', '.join(process_name)}[/b yellow]" if process_name else "all processes"

    print(f"Attempting to start {process_desc} in {target_desc}...")
    execute_parallel_command(
        services_to_target,
        util_start_service,
        action_verb="starting",
        process_name_list=process_name,
        wait=wait
    )


@app.command()
def restart(
    service_names: Annotated[Optional[List[str]], ServiceNameArgument],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Force restart using supervisor's internal restart (if available, less common). Defaults to graceful stop/start.",
        ),
    ] = False,
    wait: Annotated[bool, WaitOption],
    # Job waiting options
    wait_jobs: Annotated[bool, WaitJobsOption],
    site_name: Annotated[Optional[str], SiteNameOption],
    wait_jobs_timeout: Annotated[int, WaitJobsTimeoutOption],
    wait_jobs_poll: Annotated[int, WaitJobsPollOption],
    wait_jobs_queue: Annotated[Optional[List[str]], WaitJobsQueueOption],
):
    """
    🔄 [blue]Restart[/blue] managed services (gracefully by default).

    Graceful restart can optionally wait for active background jobs using --wait-jobs
    before starting services again. --wait-jobs is ignored with --force.
    """
    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else service_names

    invalid_services = [s for s in services_to_target if s not in all_services and s != "_no_services_found_"]
    if invalid_services:
        print(f"[red]Error:[/red] Invalid service name(s): {', '.join(invalid_services)}")
        print(f"Available services: {', '.join(all_services) or 'None'}")
        raise typer.Exit(code=1)

    target_desc = "all services" if not service_names else f"service(s): [b cyan]{', '.join(services_to_target)}[/b cyan]"
    restart_type = "Forced" if force else "Graceful"
    print(f"Attempting {restart_type} restart for {target_desc}...")

    wait_jobs_active = wait_jobs and not force

    if wait_jobs_active:
        if not site_name:
            print("[red]Error:[/red] --site-name is required when using --wait-jobs for graceful restart.")
            raise typer.Exit(code=1)
        print("[cyan]Graceful restart with job waiting enabled.[/cyan]")

        # 1. Stop services
        print("\n[STEP 1/3] Stopping services...")
        execute_parallel_command(
            services_to_target,
            util_stop_service,
            action_verb="stopping",
            show_progress=True,
            wait=wait # Use the wait flag passed to restart
        )

        # 2. Wait for jobs
        print("\n[STEP 2/3] Waiting for active jobs...")
        print("-" * 30)
        wait_success = _run_wait_jobs(
            site_name=site_name,
            timeout=wait_jobs_timeout,
            poll_interval=wait_jobs_poll,
            queues=wait_jobs_queue
        )
        print("-" * 30)
        if not wait_success:
             print("[yellow]Continuing restart process despite job wait failure/timeout.[/yellow]")

        # 3. Start services
        print("\n[STEP 3/3] Starting services...")
        execute_parallel_command(
            services_to_target,
            util_start_service,
            action_verb="starting",
            show_progress=True,
            wait=wait # Use the wait flag passed to restart
        )
        print("\nGraceful restart sequence complete.")

    else:
        if wait_jobs and force:
            print("[yellow]Warning:[/yellow] --wait-jobs is ignored when using --force restart.")

        # Original behavior: Forced restart or graceful restart without job waiting
        execute_parallel_command(
            services_to_target,
            util_restart_service, # This handles force/graceful internally now
            action_verb="restarting",
            show_progress=True,
            force=force,
            wait=wait
        )


@app.command()
def status(
    service_names: Annotated[Optional[List[str]], ServiceNameArgument],
):
    """
    📊 [yellow]Show status[/yellow] of managed services and their processes.
    """
    all_services = get_service_names_for_completion()
    services_to_target = all_services if not service_names else service_names

    invalid_services = [s for s in services_to_target if s not in all_services and s != "_no_services_found_"]
    if invalid_services:
        print(f"[red]Error:[/red] Invalid service name(s): {', '.join(invalid_services)}")
        print(f"Available services: {', '.join(all_services) or 'None'}")
        raise typer.Exit(code=1)

    target_desc = "all services" if not service_names else f"service(s): [b cyan]{', '.join(services_to_target)}[/b cyan]"
    print(f"Fetching status for {target_desc}...")

    # Status command benefits less from the progress bar, show results directly
    execute_parallel_command(
        services_to_target,
        util_get_service_info,
        action_verb="checking status",
        show_progress=False # Don't show progress bar for status
    )
    print("Status check finished.")


# --- Main Execution Guard ---

def main():
    # Pre-populate service names and handle the "no services found" case early
    # This ensures the dynamic Enum used by Typer is correctly populated or handled.
    ServiceNamesEnum = ServiceNameEnumFactory()

    # Check if the dummy value is the only member
    if list(ServiceNamesEnum.__members__.keys()) == ["_no_services_found_"]:
         print("[bold yellow]Warning:[/bold yellow] No supervisord socket files found.")
         print(f"Looked in directory: {FM_SUPERVISOR_SOCKETS_DIR}")
         print("Ensure Frappe Manager services are running and the SUPERVISOR_SOCKET_DIR environment variable is correct (if customized).")
         # Allow the app to run so --help still works, but commands will likely fail gracefully.
         # typer.Exit(code=1) # Optionally exit here

    # Dynamically update the type hint for service_names arguments using the generated Enum
    # This is a bit of a workaround to make Typer use the dynamic Enum for choices/validation
    # Note: This might not update autocompletion perfectly in all shells after the initial run.
    for command in app.registered_commands:
        for param in command.params:
             if param.name == "service_names":
                 # We need to be careful here, Typer might internalize the type hint early.
                 # A safer approach is to handle validation within the command as done above.
                 # This dynamic update is complex and potentially fragile.
                 # Let's rely on the validation within each command instead.
                 pass # Keep validation logic within commands

    app()

if __name__ == "__main__":
    main()
