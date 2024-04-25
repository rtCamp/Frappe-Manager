import typer
from typing import Annotated, Optional
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.services_manager import ServicesEnum

services_root_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

@services_root_command.command(no_args_is_help=True)
def stop(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the services_manager.")],
):
    """Stops global services."""
    services_manager: ServicesManager = ctx.obj["services"]

    if services_manager.compose_project.is_service_running(service_name.value):
        services_manager.compose_project.stop_service(services=[service_name.value])
    else:
        richprint.exit(f"{service_name.value} is not running.")


@services_root_command.command(no_args_is_help=True)
def start(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the services_manager.")],
):
    """Starts global services."""
    services_manager: ServicesManager = ctx.obj["services"]

    if services_manager.compose_project.is_service_running(service_name.value):
        services_manager.compose_project.start_service(services=[service_name.value])
    else:
        richprint.exit(f"{service_name.value} is already running.")

@services_root_command.command(no_args_is_help=True)
def restart(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the services_manager.")],
):
    """Restarts global services."""
    services_manager: ServicesManager = ctx.obj["services"]
    services_manager.compose_project.restart_service(services=[service_name.value])


@services_root_command.command(no_args_is_help=True)
def shell(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the services_manager.")],
    user: Annotated[Optional[str], typer.Option(help="Connect as this user.")] = None,
):
    """
    Open shell for the specificed global service.
    """
    services_manager: ServicesManager = ctx.obj["services"]
    services_manager.shell(service_name.value, user)
