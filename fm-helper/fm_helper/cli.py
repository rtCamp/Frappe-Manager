import os
import sys
import json
import subprocess
import traceback
from enum import Enum
from typing import Annotated, Optional, List, Tuple

import typer
from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.panel import Panel
from rich.tree import Tree
from contextlib import nullcontext # Ensure nullcontext is imported
# Imports needed for command registration
import pkgutil
import importlib

# Use relative imports within the package
try:
    # Import from the new supervisor module
    from .supervisor import (
        get_service_names as util_get_service_names,
        stop_service as util_stop_service,
        start_service as util_start_service,
        restart_service as util_restart_service,
        get_service_info as util_get_service_info,
        signal_service as util_signal_service,
        FM_SUPERVISOR_SOCKETS_DIR,
        SupervisorError, # Import the base error
    )
    # Import the refactored function from workers module
    from .workers import wait_for_jobs_to_finish
except ImportError as e:
    # Handle case where supervisor module or its dependencies failed
    print(f"[bold red]Error:[/bold red] Failed to import supervisor module: {e}")
    print("Ensure 'supervisor' package is installed and fm-helper structure is correct.")
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
        # Use a name without leading/trailing underscores
        return Enum("ServiceNames", {"NO_SERVICES_FOUND": "No services running or found"})
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

    # Define the context manager for progress, or a dummy one using nullcontext
    progress_manager = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) if show_progress else nullcontext()

    try:
        # Use both context managers: the progress manager and the thread pool
        with progress_manager as progress, ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fm_helper_worker") as executor:
            # Add task ONLY if progress is being shown (progress will be a Progress instance)
            task_id = None # Initialize task_id
            if show_progress and progress: # Check if progress is a valid Progress object
                 task_id = progress.add_task(f"{action_verb.capitalize()} services...", total=len(services))

            # Submit tasks
            for service in services:
                future = executor.submit(command_func, service, **kwargs)
                futures[future] = service

            # Process completed tasks
            for future in as_completed(futures):
                service = futures[future]
                try:
                    result = future.result()
                    results[service] = result
                except Exception as e:
                    # Print the error message identifying the service
                    print(f"[bold red]Error {action_verb} {service}:[/bold red] {e}")
                    # Print the traceback for this specific failure
                    print(f"[red]Traceback for {service}:[/red]", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                    results[service] = None # Mark service as failed
                finally:
                    # Update progress ONLY if show_progress is True and task_id is valid
                    if show_progress and task_id is not None and progress: # Check progress again
                        progress.update(task_id, advance=1)

    finally:
        # Ensure progress bar stops even if interrupted
        # This might not be strictly necessary with transient=True and context manager
        pass


    # --- Process and Print Results ---

    # --- Process and Print Results ---

    # For status command (using util_get_service_info), print the Trees
    # Also handle the internal INFO action used by graceful_restart
    if command_func == util_get_service_info or kwargs.get('action') == 'INFO':
        # If it was the internal INFO action, don't print status trees
        if kwargs.get('action') == 'INFO':
            return results # Return raw results for internal use

        # Otherwise, print status trees for the 'status' command
        print("-" * 30)
        output_printed = False
        # Sort results by service name for consistent output
        for service in sorted(results.keys()):
            result = results.get(service) # Use .get for safety
            # Check if the result is a Tree object (meaning success or connection error handled by get_service_info)
            if isinstance(result, Tree):
                print(result) # Print the Rich Tree
                output_printed = True
            # else: Error message was already printed in the parallel executor loop.

        # If no Trees were printed
        if not output_printed:
             print("[yellow]No service status information could be retrieved.[/yellow]")
        print("-" * 30)
        # Return None or empty dict? For status, printing is the main goal.
        return None # Indicate status was printed

    # For stop/restart/signal (simple boolean results expected per service)
    elif command_func in [util_stop_service, util_restart_service, util_signal_service]:
        success_count = sum(1 for res in results.values() if res is True)
        fail_count = len(services) - success_count

        if fail_count == 0:
            print(f"[green]Successfully {action_verb} {success_count} service(s).[/green]")
        elif success_count == 0:
             print(f"[red]Failed to {action_verb} {fail_count} service(s).[/red]")
        else:
             print(f"[yellow]Finished {action_verb}: {success_count} succeeded, {fail_count} failed.[/yellow]")
    
    # --- Add new block specifically for start_service results ---
    elif command_func == util_start_service:
        # Initialize overall counts
        total_started_count = 0
        total_already_running_count = 0
        total_failed_count = 0
        services_failed_entirely: List[str] = []
        output_generated = False # Flag to track if any service details were printed

        print("\n[bold]Start Results by Service:[/bold]")

        # Sort by service name for consistent aggregation order
        for service_name in sorted(results.keys()):
            result = results[service_name]
            if isinstance(result, dict): # Check if we got the expected dictionary
                started = result.get("started", [])
                already_running = result.get("already_running", [])
                failed = result.get("failed", [])

                # Update overall counts
                total_started_count += len(started)
                total_already_running_count += len(already_running)
                total_failed_count += len(failed)

                # Print service details if anything happened
                if started or already_running or failed:
                    print(f"- [cyan]{service_name}[/cyan]:")
                    if started:
                        print(f"  - [green]Started:[/green]")
                        for process in started:
                            print(f"    - {process}")
                    if already_running:
                        print(f"  - [dim]Already Running:[/dim]")
                        for process in already_running:
                            print(f"    - {process}")
                    if failed:
                        print(f"  - [red]Failed:[/red]")
                        for process in failed:
                            print(f"    - {process}")
                    output_generated = True

            else: # Result was None (or unexpected type), indicating failure in start_service itself
                services_failed_entirely.append(service_name)
                print(f"- [bold red]{service_name}: Failed entirely.[/bold red]")
                output_generated = True

        # Print Overall Summary
        print("\n[bold]Overall Summary:[/bold]")
        summary_parts = []
        if total_started_count:
            summary_parts.append(f"[green]{total_started_count} started[/green]")
        if total_already_running_count:
            summary_parts.append(f"[dim]{total_already_running_count} already running[/dim]")
        if total_failed_count:
            summary_parts.append(f"[red]{total_failed_count} failed[/red]")
        if services_failed_entirely:
            summary_parts.append(f"[bold red]{len(services_failed_entirely)} service(s) failed entirely[/bold red]")

        if summary_parts:
            print("  " + ", ".join(summary_parts) + ".")
        elif not services: # Check if services list was empty to begin with
            pass # Initial message already handled this
        elif not output_generated: # Check if any service details were printed
            print("  [yellow]No processes were targeted for starting or required starting.[/yellow]")


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

# --- Helper for Job Waiting ---

def _run_wait_jobs(
    site_name: str,
    timeout: int,
    poll_interval: int,
    queues: Optional[List[str]] = None,
    pause_scheduler_during_wait: bool = False,
) -> Tuple[bool, Optional[int], bool]:
    """Calls the wait_for_jobs_to_finish function directly."""
    queue_desc = f"queues: {', '.join(queues)}" if queues else "all queues"
    pause_msg = " (and pausing scheduler)" if pause_scheduler_during_wait else ""
    print(f"\n[cyan]Waiting up to {timeout}s for active jobs on site '{site_name}' ({queue_desc}){pause_msg}...[/cyan]")

    try:
        result = wait_for_jobs_to_finish(
            site=site_name,
            timeout=timeout,
            poll_interval=poll_interval,
            queues=queues,
            verbose=True,
            pause_scheduler_during_wait=pause_scheduler_during_wait
        )

        status = result.get("status", "error")
        message = result.get("message", "No details provided.")
        remaining_jobs = result.get("remaining_jobs", -1)
        # Extract scheduler info, defaulting if keys are missing (shouldn't happen)
        original_state = result.get("original_scheduler_state")
        was_paused = result.get("scheduler_was_paused", False)

        if status == "success":
            print(f"[green]Success:[/green] {message}")
            return True, original_state, was_paused
        elif status == "timeout":
            print(f"[yellow]Timeout:[/yellow] {message}")
            if remaining_jobs > 0:
                print(f"  {remaining_jobs} job(s) might still be running.")
            return False, original_state, was_paused
        else:
            print(f"[red]Error waiting for jobs:[/red] {message}")
            return False, original_state, was_paused

    except ImportError as e:
        print(f"[bold red]Import Error:[/bold red] Failed to import Frappe modules. Is Frappe installed? ({e})", file=sys.stderr)
        print("  Job waiting requires access to the Frappe framework.", file=sys.stderr)
        return False, None, False
    except Exception as e:
        print(f"[bold red]Error:[/bold red] An unexpected error occurred during job waiting: {e}", file=sys.stderr)
        return False, None, False

# --- Command Discovery and Registration ---
import pkgutil
import importlib
from . import commands as commands_package # Import the commands package

def register_commands():
    """Discovers and registers command functions from the 'commands' directory."""
    package_path = commands_package.__path__
    prefix = commands_package.__name__ + "."

    for _, name, ispkg in pkgutil.iter_modules(package_path, prefix):
        if not ispkg: # Only process modules, not subpackages
            try:
                module = importlib.import_module(name)
                # Look for exported 'command' function and 'command_name'
                if hasattr(module, "command") and hasattr(module, "command_name"):
                    cmd_func = getattr(module, "command")
                    cmd_name = getattr(module, "command_name")
                    
                    # Safeguard: Ensure cmd_name is not None and is a proper string
                    if cmd_name is None:
                        print(f"[bold red]Error:[/bold red] Command name defined in module '{name}' is None. Skipping registration.")
                        continue
                    
                    if callable(cmd_func) and isinstance(cmd_name, str):
                        if not cmd_name.strip():  # Check for empty or whitespace-only strings
                            print(f"[bold red]Error:[/bold red] Command name defined in module '{name}' is empty. Skipping registration.")
                            continue
                        
                        # Register the function directly as a command, explicitly set no_args_is_help=False
                        # This allows commands to run without arguments when they have optional args
                        app.command(name=cmd_name, no_args_is_help=False)(cmd_func)
                        # print(f"Registered command: {cmd_name}") # Optional debug print
                    else:
                        print(f"[yellow]Warning:[/yellow] Skipping module '{name}': 'command' not callable or 'command_name' not a string.")
                else:
                    print(f"[yellow]Warning:[/yellow] Skipping module '{name}': Missing 'command' function or 'command_name'.")
            except Exception as e:
                print(f"[bold red]Error importing command module '{name}':[/bold red] {e}")

# --- Main Execution Guard ---

def main():
    """Main entry point for the fm-helper CLI.
    
    This function:
    1. Pre-populates the service names cache
    2. Creates the ServiceNamesEnum factory (used by command modules)
    3. Registers all commands from the commands directory
    4. Runs the Typer application
    """
    # Pre-populate service names cache by calling the completion helper
    get_service_names_for_completion()

    # Create the Enum factory (might create a dummy enum if no services found)
    # Individual command modules handle service availability checks
    ServiceNamesEnum = ServiceNameEnumFactory()

    # Dynamically register commands from the 'commands' directory
    register_commands()

    # Run the main Typer app
    app()

if __name__ == "__main__":
    main()
