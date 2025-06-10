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
from contextlib import nullcontext
import pkgutil
import importlib

try:
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
    display.error(f"Failed to import supervisor module: {e}")
    display.print("Ensure 'supervisor' package is installed and fm-helper structure is correct.")
    sys.exit(1)

_cached_service_names: Optional[List[str]] = None

def get_service_names_for_completion() -> List[str]:
    """Get service names for autocompletion."""
    global _cached_service_names
    if _cached_service_names is None:
        _cached_service_names = util_get_service_names()
    return _cached_service_names

def get_dynamic_service_name_enum():
    """Create Enum for service names."""
    service_names = get_service_names_for_completion()
    if not service_names:
        return Enum("ServiceNames", {"NO_SERVICES_FOUND": "No services running or found"})
    return Enum("ServiceNames", {name: name for name in service_names})

ServiceNameEnumFactory = get_dynamic_service_name_enum

def execute_parallel_command(
    services: List[str],
    command_func,
    action_verb: str,
    show_progress: bool = True,
    verbose: bool = False,
    return_raw_results: bool = False,
    **kwargs
):
    """Execute command across multiple services in parallel."""
    if not services:
        display.print("No services specified or found to execute command on.")
        return

    max_workers = min(max(1, os.cpu_count() or 1), len(services))

    results = {}
    futures = {}

    progress_manager = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) if show_progress else nullcontext()

    try:
        with progress_manager as progress, ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fm_helper_worker") as executor:
            task_id = None
            if show_progress and progress:
                 task_id = progress.add_task(f"{action_verb.capitalize()} services...", total=len(services))

            for service in services:
                future = executor.submit(command_func, service, **kwargs)
                futures[future] = service

            for future in as_completed(futures):
                service = futures[future]
                try:
                    result = future.result()
                    results[service] = result
                except Exception as e:
                    error_msg = str(e)
                    if "Supervisor Fault" in error_msg:
                        if "SPAWN_ERROR" in error_msg:
                            error_parts = error_msg.split("SPAWN_ERROR:", 1)
                            if len(error_parts) > 1:
                                error_msg = error_parts[1].strip()
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
                    if show_progress and task_id is not None and progress:
                        progress.update(task_id, advance=1)

    finally:
        pass

    if return_raw_results:
        return results

    if command_func == util_get_service_info or kwargs.get('action') == 'INFO':
        if kwargs.get('action') == 'INFO':
            return results

        display.print("-" * 30)
        output_printed = False
        for service in sorted(results.keys()):
            result = results.get(service)
            if isinstance(result, Tree):
                display.display_tree(result)
                output_printed = True

        if not output_printed:
             display.warning("No service status information could be retrieved.")
        display.print("-" * 30)
        return None

    elif command_func in [util_restart_service, util_signal_service]:
        success_count = sum(1 for res in results.values() if res is True)
        fail_count = len(services) - success_count

        if fail_count == 0:
            display.success(f"Successfully {action_verb} {success_count} service(s).")
        elif success_count == 0:
             display.error(f"Failed to {action_verb} {fail_count} service(s).")
        else:
             display.warning(f"Finished {action_verb}: {success_count} succeeded, {fail_count} failed.")
    
    elif command_func == util_start_service:
        total_started_count = 0
        total_already_running_count = 0
        total_failed_count = 0
        services_failed_entirely: List[str] = []
        output_generated = False

        display.heading("Start Results by Service")

        for service_name in sorted(results.keys()):
            result = results[service_name]
            if isinstance(result, dict):
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

                total_started_count += len(started)
                total_already_running_count += len(already_running)
                total_failed_count += len(failed)

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

            else:
                services_failed_entirely.append(service_name)
                display.error(f"- {display.highlight(service_name)}: Failed entirely.", prefix=False)
                output_generated = True

        display.heading("Overall Summary")
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
            display.print("  " + ", ".join(summary_parts) + ".")
        elif not services:
            pass
        elif not output_generated:
            display.warning("No processes were targeted for starting or required starting.")

    elif command_func == util_stop_service:
        total_stopped_count = 0
        total_already_stopped_count = 0
        total_failed_count = 0
        services_failed_entirely: List[str] = []
        output_generated = False

        display.heading("Stop Results by Service")

        for service_name in sorted(results.keys()):
            result = results[service_name]
            if isinstance(result, dict):
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

                total_stopped_count += len(stopped)
                total_already_stopped_count += len(already_stopped)
                total_failed_count += len(failed)

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

            else:
                services_failed_entirely.append(service_name)
                display.error(f"- {display.highlight(service_name)}: Failed entirely.", prefix=False)
                output_generated = True

        display.heading("Overall Summary")
        summary_parts = []
        if total_stopped_count:
            summary_parts.append(f"[green]{total_stopped_count} stopped[/green]")
        if total_already_stopped_count:
            summary_parts.append(f"[dim]{total_already_stopped_count} already stopped[/dim]")
        if total_failed_count:
            summary_parts.append(f"[red]{total_failed_count} failed[/red]")
        if services_failed_entirely:
            summary_parts.append(f"[bold red]{len(services_failed_entirely)} service(s) failed entirely[/bold red]")

        if summary_parts:
            display.print("  " + ", ".join(summary_parts) + ".")
        elif not services:
            pass
        elif not output_generated:
            display.warning("No processes were targeted for stopping or required stopping.")

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

@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    """Initialize shared context object."""
    if ctx.obj is None:
        ctx.obj = {}
    
    ctx.obj['display'] = DisplayManager()

import pkgutil
import importlib
from . import commands as commands_package

def register_commands():
    """Discover and register command functions from the commands directory."""
    package_path = commands_package.__path__
    prefix = commands_package.__name__ + "."

    for _, name, ispkg in pkgutil.iter_modules(package_path, prefix):
        if not ispkg:
            try:
                module = importlib.import_module(name)
                if hasattr(module, "command") and hasattr(module, "command_name"):
                    cmd_func = getattr(module, "command")
                    cmd_name = getattr(module, "command_name")
                    
                    if cmd_name is None:
                        print(f"[bold red]Error:[/bold red] Command name defined in module '{name}' is None. Skipping registration.")
                        continue
                    
                    if callable(cmd_func) and isinstance(cmd_name, str):
                        if not cmd_name.strip():
                            print(f"[bold red]Error:[/bold red] Command name defined in module '{name}' is empty. Skipping registration.")
                            continue
                        
                        app.command(name=cmd_name, no_args_is_help=False)(cmd_func)
                    else:
                        print(f"[yellow]Warning:[/yellow] Skipping module '{name}': 'command' not callable or 'command_name' not a string.")
                else:
                    print(f"[yellow]Warning:[/yellow] Skipping module '{name}': Missing 'command' function or 'command_name'.")
            except Exception as e:
                print(f"[bold red]Error importing command module '{name}':[/bold red] {e}")

# --- Main Execution Guard ---

def main():
    """Main entry point for the fm-helper CLI."""
    get_service_names_for_completion()
    ServiceNamesEnum = ServiceNameEnumFactory()
    register_commands()
    app()

if __name__ == "__main__":
    main()
