from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager


@example(
    "Start global-db only",
    "global-db",
    detail="Starts only the global-db service used to store bench databases.",
)
@example(
    "Start all global services",
    "all",
    detail="Starts all global services managed by FM (nginx-proxy, global-db, etc.).",
)
def start_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="Name of the service.")],
):
    """Starts global services."""
    services_manager: ServicesManager = ctx.obj["services"]
    output = get_global_output_handler()

    if service_name.value == ServicesEnum.all:
        for service in ServicesEnum:
            if service == ServicesEnum.all:
                continue

            if services_manager.is_service_running(service.value):
                output.print(f"Skipping already running service {service.value}")
                continue

            services_manager.start_service(services=[service.value])
            output.print(f"Started service {service.value}")
    elif not services_manager.is_service_running(service_name.value):
        services_manager.start_service(services=[service_name.value])
    else:
        output.print(f"Skipping already running service {service_name.value}")
