from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from enum import Enum
import importlib
import os
import pkgutil
from typing import List, Optional

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.tree import Tree
import typer

from . import commands as commands_package
from .display import DisplayManager, display
from .supervisor import (
    FM_SUPERVISOR_SOCKETS_DIR,
    get_service_info as util_get_service_info,
    get_service_names as util_get_service_names,
    restart_service as util_restart_service,
    signal_service as util_signal_service,
    start_service as util_start_service,
    stop_service as util_stop_service,
)

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

    results = _run_parallel_tasks(services, command_func, action_verb, show_progress, **kwargs)
    
    if return_raw_results:
        return results
    
    return _handle_command_results(results, command_func, action_verb, **kwargs)


def _run_parallel_tasks(services: List[str], command_func, action_verb: str, show_progress: bool, **kwargs):
    """Run the actual parallel execution of tasks."""
    max_workers = min(max(1, os.cpu_count() or 1), len(services))
    results = {}
    futures = {}

    progress_manager = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) if show_progress else nullcontext()

    with progress_manager as progress, ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fm_helper_worker") as executor:
        task_id = None
        if show_progress and progress:
            task_id = progress.add_task(f"{action_verb.capitalize()} services...", total=len(services))

        # Submit all tasks
        for service in services:
            future = executor.submit(command_func, service, **kwargs)
            futures[future] = service

        # Collect results
        for future in as_completed(futures):
            service = futures[future]
            try:
                result = future.result()
                results[service] = result
            except Exception as e:
                results[service] = _format_error_result(str(e))
            finally:
                if show_progress and task_id is not None and progress:
                    progress.update(task_id, advance=1)

    return results


def _format_error_result(error_msg: str) -> dict:
    """Format error messages into a standard result structure."""
    if "Supervisor Fault" in error_msg:
        if "SPAWN_ERROR" in error_msg:
            error_parts = error_msg.split("SPAWN_ERROR:", 1)
            if len(error_parts) > 1:
                error_msg = error_parts[1].strip()
                error_msg = error_msg.split(" (Service:", 1)[0].strip()
            else:
                error_msg = error_msg.replace("Supervisor Fault 50:", "")
    
    return {
        'error': error_msg,
        'failed': [],
        'started': [],
        'already_running': []
    }


def _handle_command_results(results: dict, command_func, action_verb: str, **kwargs):
    """Route results to appropriate handler based on command type."""
    if command_func == util_get_service_info or kwargs.get('action') == 'INFO':
        return _handle_info_results(results, **kwargs)
    elif command_func in [util_restart_service, util_signal_service]:
        return _handle_simple_results(results, action_verb)
    elif command_func == util_start_service:
        return _handle_start_results(results)
    elif command_func == util_stop_service:
        return _handle_stop_results(results)


def _handle_info_results(results: dict, **kwargs):
    """Handle results from info/status commands."""
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


def _handle_simple_results(results: dict, action_verb: str):
    """Handle results from restart/signal commands."""
    success_count = sum(1 for res in results.values() if res is True)
    fail_count = len(results) - success_count

    if fail_count == 0:
        display.success(f"Successfully {action_verb} {success_count} service(s).")
    elif success_count == 0:
        display.error(f"Failed to {action_verb} {fail_count} service(s).")
    else:
        display.warning(f"Finished {action_verb}: {success_count} succeeded, {fail_count} failed.")


def _handle_start_results(results: dict):
    """Handle results from start commands."""
    totals = _calculate_start_totals(results)
    services_failed_entirely = _display_start_results_by_service(results)
    _display_start_summary(totals, services_failed_entirely, len(results))


def _handle_stop_results(results: dict):
    """Handle results from stop commands."""
    totals = _calculate_stop_totals(results)
    services_failed_entirely = _display_stop_results_by_service(results)
    _display_stop_summary(totals, services_failed_entirely, len(results))


def _calculate_start_totals(results: dict) -> dict:
    """Calculate totals for start command results."""
    totals = {'started': 0, 'already_running': 0, 'failed': 0}
    
    for result in results.values():
        if isinstance(result, dict) and 'error' not in result:
            totals['started'] += len(result.get("started", []))
            totals['already_running'] += len(result.get("already_running", []))
            totals['failed'] += len(result.get("failed", []))
    
    return totals


def _calculate_stop_totals(results: dict) -> dict:
    """Calculate totals for stop command results."""
    totals = {'stopped': 0, 'already_stopped': 0, 'failed': 0}
    
    for result in results.values():
        if isinstance(result, dict) and 'error' not in result:
            totals['stopped'] += len(result.get("stopped", []))
            totals['already_stopped'] += len(result.get("already_stopped", []))
            totals['failed'] += len(result.get("failed", []))
    
    return totals


def _display_start_results_by_service(results: dict) -> List[str]:
    """Display detailed start results for each service."""
    display.heading("Start Results by Service")
    services_failed_entirely = []

    for service_name in sorted(results.keys()):
        result = results[service_name]
        if isinstance(result, dict):
            if 'error' in result and result['error']:
                display.print(f"- {display.highlight(service_name)}:")
                display.error("  - Failed:", prefix=False)
                display.print(f"    - {result['error']}")
                services_failed_entirely.append(service_name)
                continue
            
            started = result.get("started", [])
            already_running = result.get("already_running", [])
            failed = result.get("failed", [])

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
        else:
            services_failed_entirely.append(service_name)
            display.error(f"- {display.highlight(service_name)}: Failed entirely.", prefix=False)

    return services_failed_entirely


def _display_stop_results_by_service(results: dict) -> List[str]:
    """Display detailed stop results for each service."""
    display.heading("Stop Results by Service")
    services_failed_entirely = []

    for service_name in sorted(results.keys()):
        result = results[service_name]
        if isinstance(result, dict):
            if 'error' in result and result['error']:
                display.print(f"- {display.highlight(service_name)}:")
                display.error("  - Failed:", prefix=False)
                display.print(f"    - {result['error']}")
                services_failed_entirely.append(service_name)
                continue
            
            stopped = result.get("stopped", [])
            already_stopped = result.get("already_stopped", [])
            failed = result.get("failed", [])

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
        else:
            services_failed_entirely.append(service_name)
            display.error(f"- {display.highlight(service_name)}: Failed entirely.", prefix=False)

    return services_failed_entirely


def _display_start_summary(totals: dict, services_failed_entirely: List[str], total_services: int):
    """Display summary for start command."""
    display.heading("Overall Summary")
    summary_parts = []
    
    if totals['started']:
        summary_parts.append(f"[green]{totals['started']} started[/green]")
    if totals['already_running']:
        summary_parts.append(f"[dim]{totals['already_running']} already running[/dim]")
    if totals['failed']:
        summary_parts.append(f"[red]{totals['failed']} failed[/red]")
    if services_failed_entirely:
        summary_parts.append(f"[bold red]{len(services_failed_entirely)} service(s) failed entirely[/bold red]")

    if summary_parts:
        display.print("  " + ", ".join(summary_parts) + ".")
    elif total_services == 0:
        pass
    else:
        display.warning("No processes were targeted for starting or required starting.")


def _display_stop_summary(totals: dict, services_failed_entirely: List[str], total_services: int):
    """Display summary for stop command."""
    display.heading("Overall Summary")
    summary_parts = []
    
    if totals['stopped']:
        summary_parts.append(f"[green]{totals['stopped']} stopped[/green]")
    if totals['already_stopped']:
        summary_parts.append(f"[dim]{totals['already_stopped']} already stopped[/dim]")
    if totals['failed']:
        summary_parts.append(f"[red]{totals['failed']} failed[/red]")
    if services_failed_entirely:
        summary_parts.append(f"[bold red]{len(services_failed_entirely)} service(s) failed entirely[/bold red]")

    if summary_parts:
        display.print("  " + ", ".join(summary_parts) + ".")
    elif total_services == 0:
        pass
    else:
        display.warning("No processes were targeted for stopping or required stopping.")

app = typer.Typer(
    invoke_without_command=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="""
    Interact with supervisord instances managed by Frappe Manager.

    Provides commands to [red]stop[/red], [green]start[/green], [blue]restart[/blue], and check the [yellow]status[/yellow]
    of background services (like Frappe, Workers, Scheduler) running within
    the Frappe Manager Docker environment.
    """,
    epilog=f"""
    Uses supervisord socket files typically located in: {FM_SUPERVISOR_SOCKETS_DIR}
    (controlled by the SUPERVISOR_SOCKET_DIR environment variable).
    """
)

@app.callback()
def main_callback(ctx: typer.Context):
    if ctx.obj is None:
        ctx.obj = {}

    ctx.obj['display'] = DisplayManager()

def register_commands():
    """Discover and register command functions from the commands directory."""
    package_path = commands_package.__path__
    prefix = commands_package.__name__ + "."

    for _, name, ispkg in pkgutil.iter_modules(package_path, prefix):
        if not ispkg:
            module = importlib.import_module(name)

            if hasattr(module, "command") and hasattr(module, "command_name"):
                cmd_func = getattr(module, "command")
                cmd_name = getattr(module, "command_name")

                if cmd_name is None:
                    continue

                if callable(cmd_func) and isinstance(cmd_name, str):
                    if not cmd_name.strip():
                        continue
                    app.command(name=cmd_name, no_args_is_help=False)(cmd_func)
                        

def main():
    """Main entry point for the fm-helper CLI."""
    get_service_names_for_completion()
    ServiceNameEnumFactory()
    register_commands()
    app()

if __name__ == "__main__":
    main()
