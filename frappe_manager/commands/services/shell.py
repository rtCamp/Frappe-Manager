from typing import Annotated

import typer

from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager


def shell_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
    user: Annotated[str | None, typer.Option(help="Connect as this user.")] = None,
):
    """
    Open shell for the specificed global service.
    """
    services_manager: ServicesManager = ctx.obj["services"]
    services_manager.shell(service_name.value, user)
