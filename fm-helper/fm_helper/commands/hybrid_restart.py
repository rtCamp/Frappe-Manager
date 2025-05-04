import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Annotated, List, Optional, Dict, Any

import typer
from rich import print

from ..cli import (
    ServiceNameEnumFactory,
    get_service_names_for_completion,
    execute_parallel_command,
    _cached_service_names
)
from ..supervisor import (
    execute_supervisor_command,
    ProcessStates,
    FM_SUPERVISOR_SOCKETS_DIR
)

# --- Constants ---
DEFAULT_SUFFIXES = "blue,green"
DEFAULT_STATE_DIR = FM_SUPERVISOR_SOCKETS_DIR

# --- Command Registration ---
command_name = "hybrid-restart"
ServiceNamesEnum = ServiceNameEnumFactory()

def command(
    ctx: typer.Context,
    base_name: Annotated[
        str,
        typer.Argument(help="Base name of the service pair (e.g., 'bench-1' for 'bench-1-blue' and 'bench-1-green')")
    ],
    service_name: Annotated[
        Optional[ServiceNamesEnum],
        typer.Argument(
            help="Service name containing the processes. If omitted, tries to match base_name against available services.",
            autocompletion=get_service_names_for_completion
        )
    ] = None,
    suffixes: Annotated[
        str,
        typer.Option(
            "--suffixes",
            help="Comma-separated pair of suffixes (e.g., 'blue,green')"
        )
    ] = DEFAULT_SUFFIXES,
    state_dir: Annotated[
        Path,
        typer.Option(
            "--state-dir",
            help="Directory to store the active color state files"
        )
    ] = DEFAULT_STATE_DIR,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Wait for processes to complete state changes"
        )
    ] = True,
) -> None:
    """
    Restart services with blue/green deployment pattern.
    
    This command manages pairs of similar processes (e.g., blue/green) where one is
    active and one is inactive. It restarts the inactive process, waits for it to
    be ready, then switches the active state.
    """
    # Convert comma-separated suffixes to list
    suffix_pair = suffixes.split(',')
    if len(suffix_pair) != 2:
        print(f"[red]Error:[/red] --suffixes must be exactly two values separated by comma. Got: {suffixes}")
        raise typer.Exit(1)
    
    # Determine the current active color/suffix
    active_suffix = _read_active_color(base_name, state_dir, suffix_pair[0])
    inactive_suffix = suffix_pair[1] if active_suffix == suffix_pair[0] else suffix_pair[0]
    
    print(f"Current active: {base_name}-{active_suffix}")
    print(f"Target restart: {base_name}-{inactive_suffix}")
    
    # Generate full process names
    active_group = f"{base_name}-{active_suffix}"
    inactive_group = f"{base_name}-{inactive_suffix}"
    
    # Determine service name if not provided
    if service_name is None:
        available_services = get_service_names_for_completion()
        matching_services = [s for s in available_services if base_name in s]
        if len(matching_services) == 1:
            service_name = ServiceNamesEnum(matching_services[0])
        elif len(matching_services) > 1:
            print(f"[red]Error:[/red] Multiple matching services found: {', '.join(matching_services)}")
            print("Please specify the exact service name as the second argument.")
            raise typer.Exit(1)
        else:
            print(f"[red]Error:[/red] No matching service found for base name: {base_name}")
            print(f"Available services: {', '.join(available_services)}")
            raise typer.Exit(1)
    
    # Convert enum to string value
    service_name_str = service_name.value
    
    print(f"\nRestarting inactive group {inactive_group} in service {service_name_str}...")
    
    try:
        # 1. Restart the inactive group
        result = execute_supervisor_command(
            service_name_str,
            "restart",
            process_names=[inactive_group],
            wait=wait
        )
        if not result:
            print(f"[red]Error:[/red] Failed to restart {inactive_group}")
            raise typer.Exit(1)
        
        # 2. Wait for the inactive group to be fully running
        if wait:
            print(f"Waiting for {inactive_group} to be ready...")
            if not _wait_for_group_running(service_name_str, inactive_group, timeout=30):
                print(f"[red]Error:[/red] {inactive_group} did not reach RUNNING state")
                raise typer.Exit(1)
        
        # 3. Update the state file to mark the new active color
        print(f"Updating active state to: {inactive_suffix}")
        _write_active_color(base_name, state_dir, inactive_suffix)
        
        print(f"[green]Success![/green] Switched active group to: {inactive_group}")
        
    except Exception as e:
        print(f"[red]Error during hybrid restart:[/red] {str(e)}")
        raise typer.Exit(1)

# --- State File Helpers ---
def _get_state_file_path(base_name: str, state_dir: Path) -> Path:
    """Get the path to the state file for a base process name."""
    return state_dir / f"{base_name}.active"

def _read_active_color(base_name: str, state_dir: Path, default_color: str) -> str:
    """Read the active color from state file, return default if not found."""
    try:
        state_file = _get_state_file_path(base_name, state_dir)
        if state_file.exists():
            return state_file.read_text().strip()
    except Exception as e:
        print(f"[yellow]Warning:[/yellow] Could not read state for {base_name}: {e}")
    return default_color

def _write_active_color(base_name: str, state_dir: Path, color: str) -> None:
    """Write the active color to the state file."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = _get_state_file_path(base_name, state_dir)
        state_file.write_text(color)
    except Exception as e:
        print(f"[yellow]Warning:[/yellow] Could not write state for {base_name}: {e}")

def _wait_for_group_running(service_name: str, group_name: str, timeout: int) -> bool:
    """Wait for all processes in a group to be RUNNING."""
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            all_info = execute_supervisor_command(service_name, "info")
            if not all_info:
                print(f"  [yellow]Warning:[/yellow] No process info returned while waiting for {group_name}.")
                return False

            group_processes = [p for p in all_info if p.get('group') == group_name]
            if not group_processes:
                print(f"  [yellow]Warning:[/yellow] No processes found for group {group_name}.")
                return False

            all_running = all(p.get('state') == ProcessStates.RUNNING for p in group_processes)
            if all_running:
                return True

        except Exception as e:
            print(f"  [red]Error checking status:[/red] {e}")
            return False

        time.sleep(1)

    return False
