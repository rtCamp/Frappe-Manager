from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager


@example(
    "Restart global-db only",
    "global-db",
    detail="Restarts the global-db service only.",
)
@example(
    "Restart all global services",
    "all",
    detail="Restarts all managed global services.",
)
def restart_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
):
    """Restarts global services."""
    services_manager: ServicesManager = ctx.obj["services"]
    output = get_global_output_handler()

    if service_name.value == ServicesEnum.all:
        for service in ServicesEnum:
            if service == ServicesEnum.all:
                continue

            services_manager.restart_service(services=[service.value])
            output.print(f"Restarted service {service.value}")
    else:
        services_manager.restart_service(services=[service_name.value])
        output.print(f"Restarted service {service_name.value}")
