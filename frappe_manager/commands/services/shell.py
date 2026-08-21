from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.services_manager import ServicesEnum
from frappe_manager.services_manager.services import ServicesManager


@example(
    "Open a shell in the global database",
    "global-db",
)
@example(
    "Open a shell in the proxy",
    "global-nginx-proxy",
)
def shell_services(
    ctx: typer.Context,
    service_name: Annotated[ServicesEnum, typer.Argument(help="One service; all is not accepted here.")],
    user: Annotated[
        str | None, typer.Option(help="Run the shell as this user instead of the container's default.")
    ] = None,
):
    """Open a bash shell in one of the global service containers."""
    # `all` is a valid ServicesEnum value but there is no shell for it: passing it through ran
    # `docker compose exec all /bin/bash`, whose "no such service: all" was swallowed and
    # reported as "Shell exited with error code: 1", as if the shell had run.
    if service_name == ServicesEnum.all:
        get_global_output_handler().display_error(
            f"shell requires a single service; {ServicesEnum.all.value!r} is not supported"
        )
        raise typer.Exit(1)

    services_manager: ServicesManager = ctx.obj["services"]
    services_manager.shell(service_name.value, user)
