#!/workspace/frappe-bench/env/bin/python3

from pathlib import Path
from typing import Annotated, Optional
import os
import rich
from rich.tree import Tree
from rich.table import Table
from rich import print
from xmlrpc.client import Fault, ServerProxy
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed

import supervisor
from supervisor import xmlrpc as sxml

import typer


FM_SUPERVISOR_SOCKETS_DIR = Path(
    os.environ.get(
        "SUPERVISOR_SOCKET_DIR", "/workspace/frappe-bench/config/fm-supervisord-sockets"
    )
)

def get_xml_connection(service_name):
    return ServerProxy(
        "http://127.0.0.1",
        transport=sxml.SupervisorTransport(
            None, None, f"unix://{FM_SUPERVISOR_SOCKETS_DIR}/{service_name}.sock"
        ),
    )

def is_supervisord_running(service_name, interval: int = 2, timeout: int = 30):
    """Check if supervisord is running and responding for the given service
    
    Args:
        service_name: Name of the service to check
        interval: Time in seconds between retry attempts
        timeout: Total time in seconds to keep trying
    """
    import time
    start_time = time.time()
    
    while True:
        try:
            conn = get_xml_connection(service_name)
            # Try to make a simple API call
            conn.supervisor.getState()
            return True
        except (OSError, IOError) as e:
            # Socket connection refused or not found
            if "Connection refused" in str(e):
                if time.time() - start_time > timeout:
                    print(f"[yellow]Warning:[/yellow] Timed out waiting for supervisord to accept connections after {timeout} seconds")
                    return False
                time.sleep(interval)
                continue
            return False
        except Exception:
            # Any other unexpected errors
            return False


def get_service_names():
    return [
        str(file.name).replace(".sock", "")
        for file in FM_SUPERVISOR_SOCKETS_DIR.glob("*.sock")
    ]


def get_service_name_enum():
    service_names = get_service_names()
    return Enum("ServiceNames", {name: name for name in service_names})


ServiceNamesEnum = get_service_name_enum()


def handle_fault(e):
    if "BAD_NAME" in e.faultString:
        print("The provided process is not available in supervisord.")
    else:
        print(f"Supervisord encountered an error: '{e.faultString}'. Please retry.")


def execute_supervisor_command(service_name, action, process_names=None, force=False):
    """Execute supervisor commands with proper error handling"""
    if not is_supervisord_running(service_name):
        print(f"[red]Error:[/red] Supervisord not running for {service_name}")
        return None
        
    conn = get_xml_connection(service_name)
    try:
        if action == "stop":
            if process_names:
                for process in process_names:
                    try:
                        conn.supervisor.stopProcess(process)
                        print(f"Stopped process [b green]{process}[/b green] in {service_name}")
                    except Fault as e:
                        if "BAD_NAME" in e.faultString:
                            processes_info = conn.supervisor.getAllProcessInfo()
                            process_info = next(
                                (info for info in processes_info if info["name"] == process),
                                None
                            )
                            if process_info:
                                full_name = f"{process_info['group']}:{process}"
                                conn.supervisor.stopProcess(full_name)
                                print(f"Stopped process [b green]{process}[/b green] in {service_name}")
            else:
                conn.supervisor.stopAllProcesses()
                print(f"Stopped all processes in [b green]{service_name}[/b green]")
        
        elif action == "restart":
            if force:
                if conn.supervisor.restart():
                    print(f"Restarted [b green]{service_name}")
                else:
                    print("Supervisord encountered an error during restart. Please retry.")
            else:
                rich.print(f"[b blue]{service_name}[/b blue] - Stopping all processes")
                conn.supervisor.stopAllProcesses()
                rich.print(f"[b blue]{service_name}[/b blue] - Starting all processes")
                conn.supervisor.startAllProcesses()
        
        elif action == "info":
            return conn.supervisor.getAllProcessInfo()
            
    except Fault as e:
        handle_fault(e)
    except Exception as e:
        print(f"[red]Error executing {action} on {service_name}: {str(e)}[/red]")
    
    return None

def stop_service(service_name, process_name_list=[]):
    execute_supervisor_command(service_name, "stop", process_names=process_name_list)

def restart_service(service_name, force=False):
    execute_supervisor_command(service_name, "restart", force=force)

def get_service_info(service_name):
    if not is_supervisord_running(service_name):
        return Tree(f"📄 [b red]{service_name} - Supervisord not running[/b red]", highlight=True)
    conn = get_xml_connection(service_name)
    root = Tree(f"📄 [b magenta]{service_name}[/b magenta]", highlight=True)
    try:
        processes = conn.supervisor.getAllProcessInfo()
        for process in processes:
            # Create a subtree for each process
            process_name = process.get('name')
            state = process.get('statename', 'UNKNOWN')
            state_color = 'green' if state == 'RUNNING' else 'red'
            
            process_tree = root.add(f"[b cyan]Process:[/b cyan] [b]{process_name}[/b] ([{state_color}]{state}[/{state_color}])")
            
            # Add process details as children
            details_table = Table(
                show_lines=False,
                show_edge=False,
                pad_edge=False,
                show_header=False,
                box=None,
            )
            details_table.add_column(style="bold")
            details_table.add_column()

            fields = [
                ("group", "Group"),
                ("pid", "PID"),
                ("stdout_logfile", "Stdout"),
                ("stderr_logfile", "Stderr"),
                ("description", "Description"),
            ]
            
            for field, label in fields:
                value = process.get(field)
                if value:
                    details_table.add_row(label, str(value))
            
            process_tree.add(details_table)
    except Fault as e:
        root.add(f"Supervisord encountered an error: '{e.faultString}'. Please retry.")
    return root


app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


def execute_parallel_command(services, command_func, **kwargs):
    """Execute a command in parallel across multiple services"""
    max_workers = min(len(services), os.cpu_count() or 1)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_service = {
            executor.submit(command_func, service, **kwargs): service 
            for service in services
        }
        
        for future in as_completed(future_to_service):
            service = future_to_service[future]
            try:
                result = future.result()
                if result is not None:  # For status command
                    print(result)
                    print()
            except Exception as e:
                print(f"[red]Error processing {service}: {str(e)}[/red]")

@app.command()
def stop(
    service_names: Annotated[
        Optional[list[ServiceNamesEnum]],
        typer.Argument(help="Names of services to stop", autocompletion=get_service_names),
    ] = None,
    process_name: Annotated[
        Optional[list[str]],
        typer.Option(
            "--process",
            "-p",
            help="Name of the process",
        ),
    ] = [],
):
    """Stop Frappe-Manager managed services. If no services specified, stops all."""
    services = get_service_names() if service_names is None else [s.value for s in service_names]
    execute_parallel_command(services, stop_service, process_name_list=process_name)

@app.command()
def restart(
    service_names: Annotated[
        Optional[list[ServiceNamesEnum]],
        typer.Argument(help="Names of services to restart", autocompletion=get_service_names),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            help="Forcefully restart the services without stopping all processes first"
        ),
    ] = False,
):
    """Restart or Start Frappe-Manager managed services. If no services specified, restarts all."""
    services = get_service_names() if service_names is None else [s.value for s in service_names]
    execute_parallel_command(services, restart_service, force=force)

@app.command()
def status(
    service_name: Annotated[
        Optional[ServiceNamesEnum],
        typer.Argument(help="Name of the service", autocompletion=get_service_names),
    ] = None,
):
    """Shows Frappe-Manager managed services status. If no service specified, shows all."""
    services = [service_name.value] if service_name is not None else get_service_names()
    execute_parallel_command(services, get_service_info)


if __name__ == "__main__":
    app()
