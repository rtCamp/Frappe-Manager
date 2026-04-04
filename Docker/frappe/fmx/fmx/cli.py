from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from enum import Enum
import functools
import importlib
import logging
import os
from pathlib import Path
import pkgutil
import threading
import time
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


def _make_restart_callback(lock: threading.Lock):
    from rich.console import Console

    _cb_console = Console(highlight=False)

    def _cb(service_name, process_name, phase, pid, method, elapsed):
        if phase == "stop":
            icon = "⏸ "
            arrow = "→"
        else:
            icon = "▶ "
            arrow = "↑"

        svc = service_name.ljust(14)
        pid_s = (f"pid {pid}" if pid else "pid ---").ljust(10)
        meth = method.ljust(16)

        if "failed" in method:
            icon = "✘ "
            method_col = f"[red]{meth}[/red]"
        elif "already" in method:
            icon = "○ "
            method_col = f"[dim]{meth}[/dim]"
        elif "USR1" in method:
            method_col = f"[yellow]{meth}[/yellow]"
        else:
            method_col = f"[dim]{meth}[/dim]"

        line = (
            f"  {icon} [bold magenta]{svc}[/bold magenta]"
            f"  [dim]{pid_s}[/dim]  [dim]{arrow}[/dim]"
            f"  {method_col}  [dim]{elapsed:.1f}s[/dim]"
            f"  [dim]{process_name}[/dim]"
        )
        with lock:
            _cb_console.print(line)

    return _cb


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

    _overall_start = None
    if command_func == util_restart_service:
        _lock = threading.Lock()
        kwargs['progress_callback'] = _make_restart_callback(_lock)
        show_progress = False
        _overall_start = time.time()

    results = _run_parallel_tasks(services, command_func, action_verb, show_progress, **kwargs)

    if return_raw_results:
        return results

    _elapsed = (time.time() - _overall_start) if _overall_start is not None else None
    return _handle_command_results(results, command_func, action_verb, elapsed=_elapsed, **kwargs)


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


def _handle_command_results(results: dict, command_func, action_verb: str, elapsed=None, **kwargs):
    """Route results to appropriate handler based on command type."""
    if command_func == util_get_service_info or kwargs.get('action') == 'INFO':
        return _handle_info_results(results, **kwargs)
    elif command_func == util_restart_service:
        return _handle_restart_results(results, elapsed=elapsed)
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


def _handle_start_results(results: dict):
    _display_action_results_by_service(
        results,
        action_key="started",
        already_key="already_running",
        heading_label="Start Results",
        action_label="started",
        already_label="already running",
    )


def _handle_stop_results(results: dict):
    _display_action_results_by_service(
        results,
        action_key="stopped",
        already_key="already_stopped",
        heading_label="Stop Results",
        action_label="stopped",
        already_label="already stopped",
    )


def _display_action_results_by_service(
    results: dict,
    action_key: str,
    already_key: str,
    heading_label: str,
    action_label: str,
    already_label: str,
):
    """Render per-service start/stop results in a consistent format.

    Args:
        results: Mapping of service_name → result dict (from execute_parallel_command).
        action_key: Key for the primary action list in each result (e.g. "started", "stopped").
        already_key: Key for the already-done list (e.g. "already_running", "already_stopped").
        heading_label: Section heading string (e.g. "Start Results").
        action_label: Human-readable label for action_key items in count summary.
        already_label: Human-readable label for already_key items in count summary.
    """
    display.heading(heading_label)

    for service_name in sorted(results.keys()):
        result = results[service_name]
        if isinstance(result, dict):
            if 'error' in result and result['error']:
                display.print(f"  [red]✘[/red] {display.highlight(service_name)}: failed entirely ({result['error']})")
                continue

            actioned = result.get(action_key, [])
            already = result.get(already_key, [])
            failed = result.get("failed", [])

            status_counts = []
            if actioned:
                status_counts.append(f"{len(actioned)} {action_label}")
            if already:
                status_counts.append(f"{len(already)} {already_label}")
            if failed:
                status_counts.append(f"{len(failed)} failed")

            if not status_counts:
                continue

            if failed or (actioned and already):
                icon = "[yellow]⚠[/yellow]"
            elif already:
                icon = "[dim]○[/dim]"
            else:
                icon = "[green]✔[/green]"

            display.print(f"  {icon} {display.highlight(service_name)}: {', '.join(status_counts)}")

            if len(status_counts) > 1 or failed:
                if actioned:
                    display.print(f"    {action_label}: {', '.join(actioned)}")
                if already:
                    display.dimmed(f"    {already_label}: {', '.join(already)}")
                if failed:
                    display.print(f"    [red]failed: {', '.join(failed)}[/red]")
            else:
                processes = actioned or already
                display.print(f"    → {', '.join(processes)}")
        else:
            display.print(f"  [red]✘[/red] {display.highlight(service_name)}: failed entirely")


def _handle_restart_results(results: dict, elapsed=None):
    total_services = len(results)
    failed_services = []
    total_started = 0

    for svc, result in results.items():
        if isinstance(result, dict) and not result.get('error'):
            total_started += len(result.get('started', [])) + len(result.get('already_running', []))
            if result.get('failed'):
                failed_services.append(svc)
        else:
            failed_services.append(svc)

    elapsed_str = f"  [dim]({elapsed:.1f}s)[/dim]" if elapsed is not None else ""

    if not failed_services:
        display.print(
            f"\n[green]✔[/green]  Restarted [bold]{total_services}[/bold] service(s) · [bold]{total_started}[/bold] process(es){elapsed_str}"
        )
    else:
        ok = total_services - len(failed_services)
        display.print(
            f"\n[yellow]⚠[/yellow]  Restarted [bold]{ok}/{total_services}[/bold] service(s){elapsed_str}"
            f"  —  [red]{', '.join(failed_services)}[/red] failed"
        )


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
