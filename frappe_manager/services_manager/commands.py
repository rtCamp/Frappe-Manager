import typer
from typing import Annotated, Optional
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.services_manager import ServicesEnum

services_root_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

@services_root_command.command(no_args_is_help=True)
def stop(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
):
    """Stops global services."""
    services_manager: ServicesManager = ctx.obj["services"]
    if service_name.value == ServicesEnum.all:
        for service in ServicesEnum:
            if service == ServicesEnum.all:
                continue

            if not services_manager.compose_project.is_service_running(service.value):
                richprint.print(f"Skipping not running service {service.value}.")
                continue

            services_manager.compose_project.stop_service(services=[service.value])
            richprint.print(f"Stopped service {service.value}.")
    else:
        if services_manager.compose_project.is_service_running(service_name.value):
            services_manager.compose_project.stop_service(services=[service_name.value])
        else:
            richprint.print(f"Skipping already stopped service {service_name.value}.")


@services_root_command.command(no_args_is_help=True)
def start(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
):
    """Starts global services."""
    services_manager: ServicesManager = ctx.obj["services"]

    if service_name.value == ServicesEnum.all:

        for service in ServicesEnum:
            if service == ServicesEnum.all:
                continue

            if services_manager.compose_project.is_service_running(service.value):
                richprint.print(f"Skipping already running service {service.value}.")
                continue

            services_manager.compose_project.start_service(services=[service.value])
            richprint.print(f"Started service {service.value}.")
    else:
        if not services_manager.compose_project.is_service_running(service_name.value):
            services_manager.compose_project.start_service(services=[service_name.value])
        else:
            richprint.print(f"Skipping already running service {service_name.value}.")

@services_root_command.command(no_args_is_help=True)
def restart(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
):
    """Restarts global services."""
    services_manager: ServicesManager = ctx.obj["services"]

    if service_name.value == ServicesEnum.all:

        for service in ServicesEnum:
            if service == ServicesEnum.all:
                continue

            services_manager.compose_project.restart_service(services=[service.value])
            richprint.print(f"Restarted service {service.value}.")
    else:
        services_manager.compose_project.restart_service(services=[service_name.value])
        richprint.print(f"Restarted service {service_name.value}.")


@services_root_command.command(no_args_is_help=True)
def shell(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
    user: Annotated[Optional[str], typer.Option(help="Connect as this user.")] = None,
):
    """
    Open shell for the specificed global service.
    """
    services_manager: ServicesManager = ctx.obj["services"]
    services_manager.shell(service_name.value, user)
