from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager


@example(
    "Apply a change to the proxy",
    "global-nginx-proxy",
    detail="A restart is what puts a new proxy config into effect, for instance after fm self real-ip.",
)
@example(
    "Restart the whole global stack",
    "all",
    detail="Benches are unreachable until the proxy is back up.",
)
def restart_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument()],
):
    """Restart the global services shared by every bench."""
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
