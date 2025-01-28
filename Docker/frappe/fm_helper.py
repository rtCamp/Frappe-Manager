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


def stop_service(service_name, process_name_list=[]):
    if not is_supervisord_running(service_name):
        print(f"[red]Error:[/red] Supervisord not running for {service_name}")
        return
    conn = get_xml_connection(service_name)
    try:
        processes_info = conn.supervisor.getAllProcessInfo()
        if process_name_list:
            for process in process_name_list:
                process_group = next(
                    (
                        info["group"]
                        for info in processes_info
                        if info["name"] == process
                    ),
                    None,
                )
                if process_group:
                    conn.supervisor.stopProcess(f"{process_group}:{process}")
                else:
                    print(
                        f"The provided process {process} is not available in supervisord."
                    )
            print(f"Stopped [b green]{service_name} - {process_name_list}")
        else:
            conn.supervisor.stopAllProcesses()
            print(f"Stopped [b green]{service_name}")
    except Fault as e:
        handle_fault(e)


def restart_service(service_name, force=False):
    if not is_supervisord_running(service_name):
        print(f"[red]Error:[/red] Supervisord not running for {service_name}")
        return
    conn = get_xml_connection(service_name)
    try:
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
    except Fault as e:
        handle_fault(e)


def get_service_info(service_name):
    if not is_supervisord_running(service_name):
        return Tree(f"📄 [b red]{service_name} - Supervisord not running[/b red]", highlight=True)
    conn = get_xml_connection(service_name)
    root = Tree(f"📄 [b magenta]{service_name}[/b magenta]", highlight=True)
    try:
        processes = conn.supervisor.getAllProcessInfo()
        for process in processes:
            status_table = Table(
                show_lines=False,
                show_edge=False,
                pad_edge=False,
                show_header=False,
                box=None,
            )
            status_table.add_column(style="bold")
            status_table.add_column()

            fields = [
                ("name", "name"),
                ("statename", "state"),
                ("stdout_logfile", "stdout"),
                ("stderr_logfile", "stderr"),
                ("description", "status"),
            ]
            for field, label in fields:
                value = process.get(field)
                if value:
                    status_table.add_row(
                        label, f"[bold]{value}[/bold]" if label == "name" else value
                    )

            root.add(status_table)
    except Fault as e:
        root.add(f"Supervisord encountered an error: '{e.faultString}'. Please retry.")
    return root


app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


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
    services_to_stop = get_service_names() if service_names is None else [s.value for s in service_names]
    
    if service_names is None:
        rich.print(" Process names will not be considered when stopping all services.")
    
    max_workers = min(len(services_to_stop), os.cpu_count() or 1)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_service = {
            executor.submit(stop_service, service, process_name): service 
            for service in services_to_stop
        }
        
        for future in as_completed(future_to_service):
            service = future_to_service[future]
            try:
                future.result()
            except Exception as e:
                print(f"[red]Error stopping {service}: {str(e)}[/red]")


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
    services_to_restart = get_service_names() if service_names is None else [s.value for s in service_names]
    
    # Dynamically set max_workers based on number of services
    # Use min to avoid creating too many threads
    max_workers = min(len(services_to_restart), os.cpu_count() or 1)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all restart tasks
        future_to_service = {
            executor.submit(restart_service, service, force): service 
            for service in services_to_restart
        }
        
        # Process results as they complete
        for future in as_completed(future_to_service):
            service = future_to_service[future]
            try:
                future.result()  # This will raise any exceptions that occurred
            except Exception as e:
                print(f"[red]Error restarting {service}: {str(e)}[/red]")


@app.command()
def status(
    service_name: Annotated[
        Optional[ServiceNamesEnum],
        typer.Argument(help="Name of the service", autocompletion=get_service_names),
    ] = None,
):
    """Shows Frappe-Manager managed services status. If no service specified, shows all."""
    services_to_check = [service_name.value] if service_name is not None else get_service_names()
    
    max_workers = min(len(services_to_check), os.cpu_count() or 1)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all status check tasks
        future_to_service = {
            executor.submit(get_service_info, service): service 
            for service in services_to_check
        }
        
        # Process and print results as they complete
        for future in as_completed(future_to_service):
            service = future_to_service[future]
            try:
                result = future.result()
                print(result)
                print()
            except Exception as e:
                print(f"[red]Error getting status for {service}: {str(e)}[/red]")


if __name__ == "__main__":
    app()
