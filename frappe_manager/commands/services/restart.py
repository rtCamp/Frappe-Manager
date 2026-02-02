import typer
from typing import Annotated
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager import ServicesEnum


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
