import os
import time
from pathlib import Path
from xmlrpc.client import Fault, ServerProxy, ProtocolError
from typing import List, Optional, Dict, Any

from rich import print
from rich.tree import Tree
from rich.table import Table

# Import supervisor safely, providing guidance if not installed
try:
    import supervisor
    from supervisor import xmlrpc as sxml
except ImportError:
    print("[bold red]Error:[/bold red] The 'supervisor' package is required but not installed.")
    print("Please install it, for example: pip install supervisor")
    # Optionally raise an exception or exit if supervisor is absolutely critical at import time
    # raise ImportError("supervisor package not found")
    # For now, we allow the import to fail softly, errors will occur during runtime if used.
    sxml = None # Define sxml as None to avoid NameError later if supervisor failed to import

# --- Constants ---

FM_SUPERVISOR_SOCKETS_DIR = Path(
    os.environ.get("SUPERVISOR_SOCKET_DIR", "/fm-sockets")
)

# --- Helper Functions ---

def get_xml_connection(service_name: str) -> Optional[ServerProxy]:
    """Get an XML-RPC connection to the supervisord instance for the given service."""
    if not sxml: # Check if supervisor import failed
        print("[red]Error:[/red] Supervisor library not available.")
        return None
    socket_path = FM_SUPERVISOR_SOCKETS_DIR / f"{service_name}.sock"
    if not socket_path.exists():
        # Don't print error here, let caller decide based on context
        return None
    try:
        return ServerProxy(
            "http://127.0.0.1", # Placeholder URL, not used for UNIX sockets
            transport=sxml.SupervisorTransport(
                serverurl=f"unix://{socket_path.resolve()}"
            ),
        )
    except Exception as e:
        print(f"[red]Error creating XML-RPC transport for {service_name}: {e}[/red]")
        return None


def is_supervisord_running(service_name: str, interval: int = 1, timeout: int = 5) -> bool:
    """Check if supervisord is running and responding for the given service."""
    start_time = time.time()
    conn = None # Initialize conn to None

    while time.time() - start_time < timeout:
        conn = get_xml_connection(service_name)
        if conn:
            try:
                # Try a simple API call
                conn.supervisor.getState()
                return True
            except Fault as e:
                # XML-RPC Fault (e.g., authentication error if configured, unlikely here)
                print(f"[yellow]Warning:[/yellow] XML-RPC Fault connecting to supervisord ({service_name}): {e.faultString}")
                return False # Usually indicates a fundamental issue
            except ProtocolError as e:
                # Error related to the HTTP/XML-RPC protocol itself
                 print(f"[yellow]Warning:[/yellow] Protocol error connecting to supervisord ({service_name}): {e}. Is supervisord running?")
                 # Continue retrying as it might be starting up
            except (OSError, IOError, ConnectionRefusedError) as e:
                # Socket errors (doesn't exist, connection refused)
                # print(f"[dim]Retrying connection to {service_name} ({e})...[/dim]") # Optional debug
                pass # Socket exists but connection refused, retry
            except Exception as e:
                # Any other unexpected errors
                print(f"[yellow]Warning:[/yellow] Unexpected error checking supervisord ({service_name}): {e}")
                return False # Unexpected error, stop checking
        else:
            # get_xml_connection returned None (socket likely doesn't exist)
            pass # Socket doesn't exist yet, retry

        time.sleep(interval)

    # Timeout reached
    if conn is None:
         print(f"[yellow]Warning:[/yellow] Timed out waiting for supervisord socket for [b magenta]{service_name}[/b magenta] after {timeout} seconds.")
    else:
         print(f"[yellow]Warning:[/yellow] Timed out waiting for supervisord [b magenta]{service_name}[/b magenta] to respond after {timeout} seconds.")
    return False


def get_service_names() -> List[str]:
    """Get a list of service names based on available socket files."""
    if not FM_SUPERVISOR_SOCKETS_DIR.is_dir():
        # Only print warning if the directory itself is missing
        if not FM_SUPERVISOR_SOCKETS_DIR.exists():
             print(f"[yellow]Warning:[/yellow] Supervisor socket directory not found: {FM_SUPERVISOR_SOCKETS_DIR}")
        return []
    return sorted([
        file.stem # Use stem to get name without extension
        for file in FM_SUPERVISOR_SOCKETS_DIR.glob("*.sock")
        if file.is_socket() # Ensure it's actually a socket file
    ])


def handle_fault(e: Fault, service_name: str, action: str, process_name: Optional[str] = None):
    """Handle specific XML-RPC Faults from supervisord."""
    fault_code = getattr(e, 'faultCode', None) # faultCode might not always be present
    fault_string = getattr(e, 'faultString', 'Unknown Fault')

    msg_prefix = f"Error during '{action}' on [b magenta]{service_name}[/b magenta]"
    if process_name:
        msg_prefix += f" for process [b yellow]'{process_name}'[/b yellow]"

    # Use standard supervisor fault names/codes if possible
    # See: http://supervisord.org/api.html#faults
    if "BAD_NAME" in fault_string:
        print(f"{msg_prefix}: Process not found.")
    elif "BAD_ARGUMENTS" in fault_string:
        print(f"{msg_prefix}: Invalid arguments provided to supervisor.")
    elif "NO_FILE" in fault_string:
        print(f"{msg_prefix}: Socket file error: {fault_string}")
    elif "NOT_RUNNING" in fault_string:
        print(f"{msg_prefix}: Process is not running.")
    elif "ALREADY_STARTED" in fault_string:
        print(f"{msg_prefix}: Process is already started.")
    elif "ALREADY_ADDED" in fault_string:
        print(f"{msg_prefix}: Process group already added.")
    elif "STILL_RUNNING" in fault_string:
        print(f"{msg_prefix}: Process is still running (cannot perform action).")
    elif "CANT_REREAD" in fault_string:
        print(f"{msg_prefix}: Cannot reread config file: {fault_string}")
    elif "NOT_EXECUTABLE" in fault_string:
        print(f"{msg_prefix}: File is not executable: {fault_string}")
    elif "FAILED" in fault_string:
         print(f"{msg_prefix}: Action failed: {fault_string}")
    elif "SHUTDOWN_STATE" in fault_string:
        print(f"{msg_prefix}: Supervisor is shutting down.")
    elif "INCORRECT_PARAMETERS" in fault_string:
         print(f"{msg_prefix}: Incorrect parameters for RPC method.")
    else:
        print(f"{msg_prefix}: Supervisor Fault {fault_code or 'N/A'}: '{fault_string}'.")


def execute_supervisor_command(
    service_name: str,
    action: str,
    process_names: Optional[List[str]] = None,
    force: bool = False,
    wait: bool = True # Default to waiting for stop/start actions
) -> Optional[Any]:
    """Execute supervisor commands with proper error handling.

    Args:
        service_name: The name of the service (supervisor instance).
        action: The command to execute ('stop', 'restart', 'info', 'start').
        process_names: List of process names to target. None means all processes.
        force: Used for restart action. True uses supervisor restart, False uses stop/start.
        wait: Wait for process start/stop operations to complete (passed to supervisor calls).

    Returns:
        Process info list for 'info', boolean status for stop/start/restart, None on failure.
    """
    if not is_supervisord_running(service_name):
        # Error message printed by is_supervisord_running
        return None

    conn = get_xml_connection(service_name)
    if not conn:
        print(f"[red]Error:[/red] Could not establish connection to [b magenta]{service_name}[/b magenta].")
        return None

    try:
        supervisor_api = conn.supervisor # Cache the proxy object

        if action == "stop":
            if process_names:
                results = {}
                for process in process_names:
                    full_process_name = f"{service_name}:{process}" # Use group:name format for clarity
                    try:
                        results[process] = supervisor_api.stopProcess(process, wait)
                        print(f"Stopped process [b green]{process}[/b green] in [b magenta]{service_name}[/b magenta]")
                    except Fault as e:
                        # Try finding the group:name format if direct name fails (less common now)
                        # This part might be redundant if supervisor expects simple name
                        handle_fault(e, service_name, action, process)
                        results[process] = False
                return all(results.values()) # Return True if all stops succeeded
            else:
                # Stop all processes
                results = supervisor_api.stopAllProcesses(wait)
                stopped_ok = all(info['statename'] == 'STOPPED' for info in results)
                if stopped_ok:
                    print(f"Stopped all processes in [b magenta]{service_name}[/b magenta]")
                else:
                    print(f"[yellow]Warning:[/yellow] Not all processes stopped successfully in [b magenta]{service_name}[/b magenta]. Check status.")
                return stopped_ok

        elif action == "start": # Added start action
             if process_names:
                results = {}
                for process in process_names:
                    try:
                        results[process] = supervisor_api.startProcess(process, wait)
                        print(f"Started process [b green]{process}[/b green] in [b magenta]{service_name}[/b magenta]")
                    except Fault as e:
                        handle_fault(e, service_name, action, process)
                        results[process] = False
                return all(results.values())
             else:
                results = supervisor_api.startAllProcesses(wait)
                started_ok = all(info['statename'] in ['RUNNING', 'STARTING'] for info in results) # Consider STARTING ok
                if started_ok:
                    print(f"Started all processes in [b magenta]{service_name}[/b magenta]")
                else:
                    print(f"[yellow]Warning:[/yellow] Not all processes started successfully in [b magenta]{service_name}[/b magenta]. Check status.")
                return started_ok

        elif action == "restart":
            if force:
                # Use supervisor's restart command (less common, might not exist)
                # Check if the method exists before calling
                if hasattr(supervisor_api, 'restart'):
                    try:
                        if supervisor_api.restart():
                            print(f"Restarted [b magenta]{service_name}[/b magenta] (forced)")
                            return True
                        else:
                            print(f"[red]Error:[/red] Supervisord ({service_name}) reported failure during forced restart.")
                            return False
                    except Fault as e:
                        handle_fault(e, service_name, action)
                        return False
                else:
                     print(f"[yellow]Warning:[/yellow] Supervisor instance for [b magenta]{service_name}[/b magenta] does not support the 'restart' command. Falling back to graceful restart.")
                     force = False # Fallback to graceful

            # Graceful restart (stop all, then start all)
            print(f"[b blue]{service_name}[/b blue] - Stopping all processes...")
            # Use internal call to stop, respecting 'wait'
            stop_success = execute_supervisor_command(service_name, "stop", wait=wait)
            if stop_success:
                print(f"[b blue]{service_name}[/b blue] - Starting all processes...")
                # Use internal call to start, respecting 'wait'
                start_success = execute_supervisor_command(service_name, "start", wait=wait)
                if start_success:
                    print(f"Gracefully restarted all processes in [b magenta]{service_name}[/b magenta]")
                else:
                     print(f"[red]Error:[/red] Failed to start processes during graceful restart of [b magenta]{service_name}[/b magenta].")
                return start_success
            else:
                print(f"[red]Error:[/red] Failed to stop processes during graceful restart of [b magenta]{service_name}[/b magenta]. Aborting start.")
                return False


        elif action == "info":
            return supervisor_api.getAllProcessInfo()

    except Fault as e:
        handle_fault(e, service_name, action, process_names[0] if process_names else None)
    except ProtocolError as e:
         print(f"[red]Error:[/red] Protocol error communicating with {service_name} during '{action}': {e}")
    except ConnectionRefusedError:
         print(f"[red]Error:[/red] Connection refused by {service_name} during '{action}'. Is supervisord running?")
    except Exception as e:
        print(f"[red]Error:[/red] Unexpected error during '{action}' on {service_name}: {e}")

    return None # Indicate failure or no return value expected


# --- Public API Functions ---

def stop_service(service_name: str, process_name_list: Optional[List[str]] = None, wait: bool = True) -> bool:
    """Stop specific processes or all processes in a service."""
    return execute_supervisor_command(service_name, "stop", process_names=process_name_list, wait=wait) or False

def start_service(service_name: str, process_name_list: Optional[List[str]] = None, wait: bool = True) -> bool:
    """Start specific processes or all processes in a service."""
    return execute_supervisor_command(service_name, "start", process_names=process_name_list, wait=wait) or False

def restart_service(service_name: str, force: bool = False, wait: bool = True) -> bool:
    """Restart a service (all its processes)."""
    return execute_supervisor_command(service_name, "restart", force=force, wait=wait) or False

def get_service_info(service_name: str) -> Optional[Tree]:
    """Get detailed information about a service and its processes as a Rich Tree."""
    if not is_supervisord_running(service_name):
        # Return a tree indicating the service is down or unreachable
        return Tree(f"📄 [b red]{service_name}[/b red] - Supervisord not running or unreachable", highlight=True)

    conn = get_xml_connection(service_name)
    if not conn:
         return Tree(f"📄 [b red]{service_name}[/b red] - Could not connect to supervisord", highlight=True)

    root = Tree(f"📄 [b magenta]{service_name}[/b magenta]", highlight=True)
    try:
        processes = conn.supervisor.getAllProcessInfo()
        if not processes:
            root.add("[i]No processes found for this service.[/i]")
            return root

        for process in processes:
            process_name = process.get('name', 'N/A')
            state = process.get('statename', 'UNKNOWN')
            state_color = 'green' if state == 'RUNNING' else ('yellow' if state in ['STARTING', 'BACKOFF', 'STOPPING'] else 'red')

            process_tree = root.add(f"[b cyan]Process:[/b cyan] [b]{process_name}[/b] ([{state_color}]{state}[/{state_color}])")

            details_table = Table(
                show_lines=False,
                show_edge=False,
                pad_edge=False,
                show_header=False,
                box=None,
                padding=(0, 1, 0, 1) # Add padding around columns
            )
            details_table.add_column(style="dim", justify="right") # Label style
            details_table.add_column() # Value style

            fields_to_display = [
                ("group", "Group"),
                ("pid", "PID"),
                # ("state", "State Code"), # Raw state code, usually less useful than statename
                ("start", "Start Time"),
                ("stop", "Stop Time"),
                ("now", "Server Time"),
                ("spawnerr", "Spawn Error"),
                ("exitstatus", "Exit Status"),
                ("stdout_logfile", "Stdout Log"),
                ("stderr_logfile", "Stderr Log"),
                ("description", "Description"),
            ]

            has_details = False
            for field, label in fields_to_display:
                value = process.get(field)
                # Only add rows for fields that have a non-zero/non-empty value, except for PID 0 and exitstatus 0
                if value or (field == 'pid' and value == 0) or (field == 'exitstatus' and value == 0):
                    # Basic formatting for timestamps
                    if field in ['start', 'stop', 'now'] and isinstance(value, int) and value > 0:
                        try:
                            import datetime
                            # Use local timezone
                            dt_object = datetime.datetime.fromtimestamp(value)
                            value = dt_object.strftime('%Y-%m-%d %H:%M:%S %Z')
                        except Exception:
                            value = f"{value} (timestamp)" # Fallback
                    elif field == 'pid' and value == 0:
                         value = "[dim]N/A (Not Running)[/dim]" # Clarify PID 0
                    elif field == 'exitstatus' and process.get('statename') == 'RUNNING':
                         continue # Don't show exit status 0 if running

                    details_table.add_row(f"{label}:", str(value))
                    has_details = True

            if has_details:
                process_tree.add(details_table)
            # else: # No need for 'no details' message if table is empty

    except Fault as e:
        handle_fault(e, service_name, "info")
        root.add(f"[red]Supervisord Error:[/red] Could not retrieve process info.")
    except ProtocolError as e:
         root.add(f"[red]Protocol Error:[/red] {e}")
    except ConnectionRefusedError:
         root.add(f"[red]Connection Refused:[/red] Could not connect to supervisord.")
    except Exception as e:
        root.add(f"[red]Unexpected Error:[/red] {str(e)}")

    return root
