import os
import sys
import subprocess
import traceback
from enum import Enum
from typing import Annotated, Optional, List, Tuple

import typer
from .display import DisplayManager, display  # Import both DisplayManager and global display instance
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
except ImportError as e:
    # Handle case where supervisor module or its dependencies failed
    display.error(f"Failed to import supervisor module: {e}")
    display.print("Ensure 'supervisor' package is installed and fm-helper structure is correct.")
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
    verbose: bool = False,
    return_raw_results: bool = False,
    **kwargs
):
    """Execute a command in parallel across multiple services with progress display."""
    if not services:
        display.print("No services specified or found to execute command on.")
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
                    # Format supervisor errors more clearly
                    error_msg = str(e)
                    if "Supervisor Fault" in error_msg:
                        # Better error message formatting
                        if "SPAWN_ERROR" in error_msg:
                            # Extract just the relevant part of the spawn error
                            error_parts = error_msg.split("SPAWN_ERROR:", 1)
                            if len(error_parts) > 1:
                                error_msg = error_parts[1].strip()
                                # Remove the service name from the error if it's duplicated
                                error_msg = error_msg.split(" (Service:", 1)[0].strip()
                            else:
                                error_msg = error_msg.replace("Supervisor Fault 50:", "")
                    
                    results[service] = {
                        'error': error_msg,
                        'failed': [],
                        'started': [],
                        'already_running': []
                    }
                finally:
                    # Update progress ONLY if show_progress is True and task_id is valid
                    if show_progress and task_id is not None and progress: # Check progress again
                        progress.update(task_id, advance=1)

    finally:
        # Ensure progress bar stops even if interrupted
        # This might not be strictly necessary with transient=True and context manager
        pass

    # Return raw results if requested
    if return_raw_results:
        return results

    # --- Process and Print Results ---

    # --- Process and Print Results ---

    # For status command (using util_get_service_info), print the Trees
    # Also handle the internal INFO action used by graceful_restart
    if command_func == util_get_service_info or kwargs.get('action') == 'INFO':
        # If it was the internal INFO action, don't print status trees
        if kwargs.get('action') == 'INFO':
            return results # Return raw results for internal use

        # Otherwise, print status trees for the 'status' command
        display.print("-" * 30)
        output_printed = False
        # Sort results by service name for consistent output
        for service in sorted(results.keys()):
            result = results.get(service) # Use .get for safety
            # Check if the result is a Tree object (meaning success or connection error handled by get_service_info)
            if isinstance(result, Tree):
                display.display_tree(result) # Print the Rich Tree
                output_printed = True
            # else: Error message was already printed in the parallel executor loop.

        # If no Trees were printed
        if not output_printed:
             display.warning("No service status information could be retrieved.")
        display.print("-" * 30)
        # Return None or empty dict? For status, printing is the main goal.
        return None # Indicate status was printed

    # For restart/signal (simple boolean results expected per service)
    elif command_func in [util_restart_service, util_signal_service]:
        success_count = sum(1 for res in results.values() if res is True)
        fail_count = len(services) - success_count

        if fail_count == 0:
            display.success(f"Successfully {action_verb} {success_count} service(s).")
        elif success_count == 0:
             display.error(f"Failed to {action_verb} {fail_count} service(s).")
        else:
             display.warning(f"Finished {action_verb}: {success_count} succeeded, {fail_count} failed.")
    
    # --- Add new block specifically for start_service results ---
    elif command_func == util_start_service:
        # Initialize overall counts
        total_started_count = 0
        total_already_running_count = 0
        total_failed_count = 0
        services_failed_entirely: List[str] = []
        output_generated = False # Flag to track if any service details were printed

        display.heading("Start Results by Service")

        # Sort by service name for consistent aggregation order
        for service_name in sorted(results.keys()):
            result = results[service_name]
            if isinstance(result, dict): # Check if we got the expected dictionary
                # Check if this is an error result
                if 'error' in result and result['error']:
                    display.print(f"- {display.highlight(service_name)}:")
                    display.error("  - Failed:", prefix=False)
                    display.print(f"    - {result['error']}")
                    output_generated = True
                    services_failed_entirely.append(service_name)
                    continue
                
                started = result.get("started", [])
                already_running = result.get("already_running", [])
                failed = result.get("failed", [])

                # Update overall counts
                total_started_count += len(started)
                total_already_running_count += len(already_running)
                total_failed_count += len(failed)

                # Print service details if anything happened
                if started or already_running or failed:
                    display.print(f"- {display.highlight(service_name)}:")
                    if started:
                        display.print("  - Started:")
                        for process in started:
                            display.print(f"    - {display.highlight(process)}")
                    if already_running:
                        display.dimmed("  - Already Running:")
                        for process in already_running:
                            display.print(f"    - {display.highlight(process)}")
                    if failed:
                        display.error("  - Failed:", prefix=False)
                        for process in failed:
                            display.print(f"    - {display.highlight(process)}")
                    output_generated = True

            else: # Result was None (or unexpected type), indicating failure in start_service itself
                services_failed_entirely.append(service_name)
                display.error(f"- {display.highlight(service_name)}: Failed entirely.", prefix=False)
                output_generated = True

        # Print Overall Summary
        display.heading("Overall Summary") # Use heading for consistency
        summary_parts = []
        if total_started_count:
            summary_parts.append(f"[green]{total_started_count} started[/green]") # Keep markup for inline styling
        if total_already_running_count:
            summary_parts.append(f"[dim]{total_already_running_count} already running[/dim]") # Keep markup
        if total_failed_count:
            summary_parts.append(f"[red]{total_failed_count} failed[/red]") # Keep markup
        if services_failed_entirely:
            summary_parts.append(f"[bold red]{len(services_failed_entirely)} service(s) failed entirely[/bold red]") # Keep markup

        if summary_parts:
            display.print("  " + ", ".join(summary_parts) + ".") # Use display.print
        elif not services: # Check if services list was empty to begin with
            pass # Initial message already handled this
        elif not output_generated: # Check if any service details were printed
            display.warning("No processes were targeted for starting or required starting.") # Use display.warning

    # --- Add new block specifically for stop_service results ---
    elif command_func == util_stop_service:
        # Initialize overall counts
        total_stopped_count = 0
        total_already_stopped_count = 0
        total_failed_count = 0
        services_failed_entirely: List[str] = []
        output_generated = False # Flag to track if any service details were printed

        display.heading("Stop Results by Service")

        # Sort by service name for consistent aggregation order
        for service_name in sorted(results.keys()):
            result = results[service_name]
            if isinstance(result, dict): # Check if we got the expected dictionary
                # Check if this is an error result
                if 'error' in result and result['error']:
                    display.print(f"- {display.highlight(service_name)}:")
                    display.error("  - Failed:", prefix=False)
                    display.print(f"    - {result['error']}")
                    output_generated = True
                    services_failed_entirely.append(service_name)
                    continue
                
                stopped = result.get("stopped", [])
                already_stopped = result.get("already_stopped", [])
                failed = result.get("failed", [])

                # Update overall counts
                total_stopped_count += len(stopped)
                total_already_stopped_count += len(already_stopped)
                total_failed_count += len(failed)

                # Print service details if anything happened
                if stopped or already_stopped or failed:
                    display.print(f"- {display.highlight(service_name)}:")
                    if stopped:
                        display.print("  - Stopped:")
                        for process in stopped:
                            display.print(f"    - {display.highlight(process)}")
                    if already_stopped:
                        display.dimmed("  - Already Stopped:")
                        for process in already_stopped:
                            display.print(f"    - {display.highlight(process)}")
                    if failed:
                        display.error("  - Failed:", prefix=False)
                        for process in failed:
                            display.print(f"    - {display.highlight(process)}")
                    output_generated = True

            else: # Result was None (or unexpected type), indicating failure in stop_service itself
                services_failed_entirely.append(service_name)
                display.error(f"- {display.highlight(service_name)}: Failed entirely.", prefix=False)
                output_generated = True

        # Print Overall Summary
        display.heading("Overall Summary") # Use heading for consistency
        summary_parts = []
        if total_stopped_count:
            summary_parts.append(f"[green]{total_stopped_count} stopped[/green]") # Keep markup for inline styling
        if total_already_stopped_count:
            summary_parts.append(f"[dim]{total_already_stopped_count} already stopped[/dim]") # Keep markup
        if total_failed_count:
            summary_parts.append(f"[red]{total_failed_count} failed[/red]") # Keep markup
        if services_failed_entirely:
            summary_parts.append(f"[bold red]{len(services_failed_entirely)} service(s) failed entirely[/bold red]") # Keep markup

        if summary_parts:
            display.print("  " + ", ".join(summary_parts) + ".") # Use display.print
        elif not services: # Check if services list was empty to begin with
            pass # Initial message already handled this
        elif not output_generated: # Check if any service details were printed
            display.warning("No processes were targeted for stopping or required stopping.") # Use display.warning


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

# --- App Callback for Context Initialization ---
@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    # Optional: Add global options here if needed, e.g., verbosity
    # verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable verbose output.")] = False,
):
    """
    Main callback to initialize shared context object.
    """
    # Initialize ctx.obj as a dictionary if None
    if ctx.obj is None:
        ctx.obj = {}
    
    # Create and attach the DisplayManager to the context dictionary
    # Pass global options like verbose if you add them
    # ctx.obj['display'] = DisplayManager(verbose=verbose)
    ctx.obj['display'] = DisplayManager() # Initialize with default verbosity for now


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
