import typer
from typing import Annotated, Optional
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.services_manager import ServicesEnum


def shell_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
    user: Annotated[Optional[str], typer.Option(help="Connect as this user.")] = None,
):
    """
    Open shell for the specificed global service.
    """
    services_manager: ServicesManager = ctx.obj["services"]
    services_manager.shell(service_name.value, user)
