from typing import Annotated
from psutil import users
import typer

from frappe_manager.global_services.GlobalServices import GlobalServices
from frappe_manager.console_manager.Richprint import richprint

global_services_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

service = None


@global_services_app.callback()
def global_services_callback(
    ctx: typer.Context,
):
    global service
    if not ctx.obj['is_help_called']:
        service = ctx.obj["services"]
        service.set_typer_context(ctx)


def validate_servicename(
    service_name: Annotated[str, typer.Argument(help="Name of the service.")]
):
    services = service.composefile.get_services_list()
    if not service_name in services:
        richprint.exit(f"{service} is not a valid service name.")
    else:
        return service_name

@global_services_app.command(no_args_is_help=True)
def stop(
    service_name: Annotated[
        str, typer.Argument(help="Name of the service.", callback=validate_servicename)
    ],
):
    """Stops global services."""
    if not service.is_service_running(service):
        service.stop(service_name)
    else:
        richprint.exit(f"{service} is not running.")


@global_services_app.command(no_args_is_help=True)
def start(
    service_name: Annotated[
        str, typer.Argument(help="Name of the service.", callback=validate_servicename)
    ],
):
    """Starts global services."""
    if service.is_service_running(service):
        service.start(service_name)
    else:
        richprint.exit(f"{service} is already running.")

@global_services_app.command(no_args_is_help=True)
def restart(
    service_name: Annotated[
        str, typer.Argument(help="Name of the service.", callback=validate_servicename)
    ],
):
    """Restarts global services."""
    service.restart(service_name)


@global_services_app.command(no_args_is_help=True)
def shell(
    service_name: Annotated[
        str, typer.Argument(help="Name of the service.", callback=validate_servicename)
    ],
    user: Annotated[str, typer.Option(help="Connect as this user.")] = None,
):
    """
    Open shell for the specificed global service.
    """
    service.shell(service_name, users)


@global_services_app.command()
def test():
    print(service.is_service_running("global-db"))
