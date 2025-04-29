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
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.panel import Panel
from contextlib import nullcontext # Ensure nullcontext is imported
# Imports needed for command registration
import pkgutil
import importlib

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
                    print(f"[bold red]Error {action_verb} {service}:[/bold red] {e}")
                    results[service] = None
                    raise e # TODO debug
                finally:
                    # Update progress ONLY if show_progress is True and task_id is valid
                    if show_progress and task_id is not None and progress: # Check progress again
                        progress.update(task_id, advance=1)

    except Exception as e:
        print(f"[bold red]An unexpected error occurred during parallel execution:[/bold red] {e}")
        raise e # TODO debug
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
