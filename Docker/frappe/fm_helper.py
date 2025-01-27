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

import supervisor
from supervisor import xmlrpc as sxml

import typer


FM_SUPERVISOR_SOCKETS_DIR = Path(
    os.environ.get(
        "SUPERVISOR_SOCKET_DIR", "/workspace/frappe-bench/config/fm-supervisor-sockets"
    )
)

def get_xml_connection(service_name):
    return ServerProxy(
        "http://127.0.0.1",
        transport=sxml.SupervisorTransport(
            None, None, f"unix://{FM_SUPERVISOR_SOCKETS_DIR}/{service_name}.sock"
        ),
    )


def get_service_names():
    return [
        str(file.name).replace(".sock", "")
        for file in FM_SUPERVISOR_SOCKETS_DIR.glob("*.sock")
    ]


def get_service_name_enum():
    service_names = get_service_names()
    service_names.append("all")
    return Enum("ServiceNames", {name: name for name in service_names})


ServiceNamesEnum = get_service_name_enum()


def handle_fault(e):
    if "BAD_NAME" in e.faultString:
        print("The provided process is not available in supervisord.")
    else:
        print(f"Supervisord encountered an error: '{e.faultString}'. Please retry.")


def stop_service(service_name, process_name_list=[]):
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
    process_name: Annotated[
        Optional[list[str]],
        typer.Option(
            "--process",
            "-p",
            help="Name of the process",
        ),
    ] = [],
    service_name: Annotated[
        ServiceNamesEnum,
        typer.Argument(help="Name of the service", autocompletion=get_service_names),
    ] = ServiceNamesEnum.all.value,
):
    """Stop Frappe-Manager managed service"""
    if service_name != ServiceNamesEnum.all:
        stop_service(service_name.value, process_name)
    else:
        rich.print(
            " Process names will not be considered unless a service name is provided."
        )
        for service in get_service_names():
            stop_service(service)


@app.command()
def restart(
    service_name: Annotated[
        ServiceNamesEnum,
        typer.Argument(help="Name of the service", autocompletion=get_service_names),
    ] = ServiceNamesEnum.all.value,
    force: Annotated[
        bool,
        typer.Option(
            help="Forcefully restart the service without stopping all processes first"
        ),
    ] = False,
):
    """Restart or Start Frappe-Manager managed service"""
    if service_name != ServiceNamesEnum.all:
        restart_service(service_name.value, force=force)
    else:
        for service in get_service_names():
            restart_service(service, force=force)


@app.command()
def status(
    service_name: Annotated[
        ServiceNamesEnum,
        typer.Argument(help="Name of the service", autocompletion=get_service_names),
    ] = ServiceNamesEnum.all.value,
):
    """Shows Frappe-Manager managed services status"""
    if service_name != ServiceNamesEnum.all:
        print(get_service_info(service_name))
    else:
        for service in get_service_names():
            print(get_service_info(service))
            print()


if __name__ == "__main__":
    app()
