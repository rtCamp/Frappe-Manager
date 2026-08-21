from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager


@example(
    "Take the global stack down",
    "all",
    detail="Services already stopped are left alone.",
)
def stop_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument()],
):
    """
    Stop the global services shared by every bench.

    Every bench is reached through global-nginx-proxy and keeps its data in global-db, so stopping these leaves the bench containers running but unreachable and without a database.
    """
    services_manager: ServicesManager = ctx.obj["services"]
    output = get_global_output_handler()
    if service_name.value == ServicesEnum.all:
        for service in ServicesEnum:
            if service == ServicesEnum.all:
                continue

            if not services_manager.is_service_running(service.value):
                output.print(f"Skipping not running service {service.value}")
                continue

            services_manager.stop_service(services=[service.value])
            output.print(f"Stopped service {service.value}")
    elif services_manager.is_service_running(service_name.value):
        services_manager.stop_service(services=[service_name.value])
        output.print(f"Stopped service {service_name.value}")
    else:
        output.print(f"Skipping already stopped service {service_name.value}")
