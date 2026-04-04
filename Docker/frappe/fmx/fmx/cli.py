from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from enum import Enum
import functools
import importlib
import logging
import os
from pathlib import Path
import pkgutil
from typing import List, Optional

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.tree import Tree
import typer

from fmx import commands as commands_package
from fmx.display import DisplayManager, display
from fmx.supervisor import (
    FM_SUPERVISOR_SOCKETS_DIR,
    get_service_info as util_get_service_info,
    get_service_names as util_get_service_names,
    restart_service as util_restart_service,
    signal_service as util_signal_service,
    start_service as util_start_service,
    stop_service as util_stop_service,
)


def setup_logging():
    """Setup logging for fmx application."""
    log_dir = Path("/workspace/frappe-bench/logs")
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_dir / "fmx.log"),
        ],
    )


@functools.lru_cache(maxsize=1)
def get_service_names_for_completion() -> List[str]:
    """Get service names for autocompletion."""
    return util_get_service_names()


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
    **kwargs,
):
    """Execute command across multiple services in parallel."""
    if not services:
        display.print("No services specified or found to execute command on.")
        return

    kwargs['verbose'] = verbose
    results = _run_parallel_tasks(services, command_func, action_verb, show_progress, **kwargs)

    if return_raw_results:
        return results

    return _handle_command_results(results, command_func, action_verb, **kwargs)


def _run_parallel_tasks(services: List[str], command_func, action_verb: str, show_progress: bool, **kwargs):
    """Run the actual parallel execution of tasks."""
    max_workers = min(max(1, os.cpu_count() or 1), len(services))
    results = {}
    futures = {}

    progress_manager = (
        Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        )
        if show_progress
        else nullcontext()
    )

    with (
        progress_manager as progress,
        ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fm_helper_worker") as executor,
    ):
        task_id = None
        if show_progress and progress:
            task_id = progress.add_task(f"{action_verb.capitalize()} services...", total=len(services))

        for service in services:
            future = executor.submit(command_func, service, **kwargs)
            futures[future] = service

        for future in as_completed(futures):
            service = futures[future]
            if show_progress and task_id is not None and progress:
                progress.update(task_id, description=f"{action_verb.capitalize()} {service}...")
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

    return {'error': error_msg, 'failed': [], 'started': [], 'already_running': []}


def _handle_command_results(results: dict, command_func, action_verb: str, **kwargs):
    """Route results to appropriate handler based on command type."""
    if command_func == util_get_service_info or kwargs.get('action') == 'INFO':
        return _handle_info_results(results, **kwargs)
    elif command_func == util_restart_service:
        return _handle_restart_results(results)
    elif command_func == util_signal_service:
        return _handle_simple_results(results, action_verb)
    elif command_func == util_start_service:
        return _handle_start_results(results)
    elif command_func == util_stop_service:
        return _handle_stop_results(results)


def _handle_info_results(results: dict, **kwargs):
    """Handle results from info/status commands."""
    if kwargs.get('action') == 'INFO':
        return results

    output_printed = False
    for service in sorted(results.keys()):
        result = results.get(service)
        if isinstance(result, Tree):
            display.display_tree(result)
            output_printed = True

    if not output_printed:
        display.warning("No service status information could be retrieved.")
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
    _display_start_results_by_service(results)


def _handle_stop_results(results: dict):
    _display_stop_results_by_service(results)


def _display_start_results_by_service(results: dict):
    display.heading("Start Results")

    for service_name in sorted(results.keys()):
        result = results[service_name]
        if isinstance(result, dict):
            if 'error' in result and result['error']:
                display.print(f"  [red]✘[/red] {display.highlight(service_name)}: failed entirely ({result['error']})")
                continue

            started = result.get("started", [])
            already_running = result.get("already_running", [])
            failed = result.get("failed", [])

            status_counts = []
            if started:
                status_counts.append(f"{len(started)} started")
            if already_running:
                status_counts.append(f"{len(already_running)} already running")
            if failed:
                status_counts.append(f"{len(failed)} failed")

            if not status_counts:
                continue

            if failed or (started and already_running):
                icon = "[yellow]⚠[/yellow]"
            elif already_running:
                icon = "[dim]○[/dim]"
            else:
                icon = "[green]✔[/green]"

            display.print(f"  {icon} {display.highlight(service_name)}: {', '.join(status_counts)}")

            if len(status_counts) > 1 or failed:
                if started:
                    display.print(f"    started: {', '.join(started)}")
                if already_running:
                    display.dimmed(f"    already running: {', '.join(already_running)}")
                if failed:
                    display.print(f"    [red]failed: {', '.join(failed)}[/red]")
            else:
                processes = started or already_running
                display.print(f"    → {', '.join(processes)}")
        else:
            display.print(f"  [red]✘[/red] {display.highlight(service_name)}: failed entirely")


def _display_stop_results_by_service(results: dict):
    display.heading("Stop Results")

    for service_name in sorted(results.keys()):
        result = results[service_name]
        if isinstance(result, dict):
            if 'error' in result and result['error']:
                display.print(f"  [red]✘[/red] {display.highlight(service_name)}: failed entirely ({result['error']})")
                continue

            stopped = result.get("stopped", [])
            already_stopped = result.get("already_stopped", [])
            failed = result.get("failed", [])

            status_counts = []
            if stopped:
                status_counts.append(f"{len(stopped)} stopped")
            if already_stopped:
                status_counts.append(f"{len(already_stopped)} already stopped")
            if failed:
                status_counts.append(f"{len(failed)} failed")

            if not status_counts:
                continue

            if failed or (stopped and already_stopped):
                icon = "[yellow]⚠[/yellow]"
            elif already_stopped:
                icon = "[dim]○[/dim]"
            else:
                icon = "[green]✔[/green]"

            display.print(f"  {icon} {display.highlight(service_name)}: {', '.join(status_counts)}")

            if len(status_counts) > 1 or failed:
                if stopped:
                    display.print(f"    stopped: {', '.join(stopped)}")
                if already_stopped:
                    display.dimmed(f"    already stopped: {', '.join(already_stopped)}")
                if failed:
                    display.print(f"    [red]failed: {', '.join(failed)}[/red]")
            else:
                processes = stopped or already_stopped
                display.print(f"    → {', '.join(processes)}")
        else:
            display.print(f"  [red]✘[/red] {display.highlight(service_name)}: failed entirely")


def _handle_restart_results(results: dict):
    stop_results = {}
    start_results = {}

    for service_name, result in results.items():
        if isinstance(result, dict) and 'error' not in result:
            stop_results[service_name] = {
                'stopped': result.get('stopped', []),
                'already_stopped': result.get('already_stopped', []),
                'failed': result.get('failed', []),
            }
            start_results[service_name] = {
                'started': result.get('started', []),
                'already_running': result.get('already_running', []),
                'failed': result.get('failed', []),
            }
        else:
            stop_results[service_name] = result
            start_results[service_name] = result

    _display_stop_results_by_service(stop_results)
    display.print("")
    _display_start_results_by_service(start_results)


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
    """,
)


@app.callback()
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode (shows stack traces)"),
):
    if ctx.obj is None:
        ctx.obj = {}

    ctx.obj['display'] = DisplayManager(verbose=verbose)
    ctx.obj['debug'] = debug


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

                if isinstance(cmd_func, typer.Typer):
                    app.add_typer(cmd_func, name=cmd_name)
                elif callable(cmd_func) and isinstance(cmd_name, str):
                    if not cmd_name.strip():
                        continue
                    app.command(name=cmd_name, no_args_is_help=False)(cmd_func)


def main():
    """Main entry point for the fmx CLI."""
    setup_logging()
    get_service_names_for_completion()
    ServiceNameEnumFactory()
    register_commands()
    app()


if __name__ == "__main__":
    main()
