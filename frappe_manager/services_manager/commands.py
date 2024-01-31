from typing import Annotated
from psutil import users
import typer

from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.services_manager import ServicesEnum

services_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

services_manager = None

@services_app.callback()
def global_services_callback(
    ctx: typer.Context,
):
    global services_manager

    if not ctx.obj['is_help_called']:
        services_manager = ctx.obj["services"]
        services_manager.set_typer_context(ctx)

def validate_servicename(
    service_name: Annotated[str, typer.Argument(help="Name of the services_manager.")]
):
    services = services_manager.composefile.get_services_list()
    if not service_name in services:
        richprint.exit(f"{service_name} is not a valid service name.")
    else:
        return service_name

@services_app.command(no_args_is_help=True)
def stop(
    service_name: Annotated[
        ServicesEnum,
        typer.Argument(help="Name of the services_manager.")
    ],
):
    """Stops global services."""
    if services_manager.is_service_running(service_name.value):
        services_manager.stop(service_name.value)
    else:
        richprint.exit(f"{service_name.value} is not running.")


@services_app.command(no_args_is_help=True)
def start(
    service_name: Annotated[
        ServicesEnum,
        typer.Argument(help="Name of the services_manager.")
    ],
):
    """Starts global services."""
    if not services_manager.is_service_running(service_name.value):
        services_manager.start(service_name.value)
    else:
        richprint.exit(f"{service_name.value} is already running.")

@services_app.command(no_args_is_help=True)
def restart(
    service_name: Annotated[
        ServicesEnum,
        typer.Argument(help="Name of the services_manager.")
    ],
):
    """Restarts global services."""
    services_manager.restart(service_name.value)


@services_app.command(no_args_is_help=True)
def shell(
    service_name: Annotated[
        ServicesEnum,
        typer.Argument(help="Name of the services_manager.")
    ],
    user: Annotated[str, typer.Option(help="Connect as this user.")] = None,
):
    """
    Open shell for the specificed global service.
    """
    services_manager.shell(service_name.value, users)
