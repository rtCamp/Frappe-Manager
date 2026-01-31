import typer
from typing import Annotated
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.services_manager import ServicesEnum


def stop_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
):
    """Stops global services."""
    services_manager: ServicesManager = ctx.obj["services"]
    if service_name.value == ServicesEnum.all:
        for service in ServicesEnum:
            if service == ServicesEnum.all:
                continue

            if not services_manager.is_service_running(service.value):
                richprint.print(f"Skipping not running service {service.value}")
                continue

            services_manager.stop_service(services=[service.value])
            richprint.print(f"Stopped service {service.value}")
    else:
        if services_manager.is_service_running(service_name.value):
            services_manager.stop_service(services=[service_name.value])
        else:
            richprint.print(f"Skipping already stopped service {service_name.value}")
