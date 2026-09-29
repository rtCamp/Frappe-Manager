"""`fm services ports`: which host ports the global proxy publishes."""

from typing import Annotated

import typer
from typer_examples import example

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import get_global_output_handler


def _validate_port(output, flag: str, value: int) -> None:
    if not 1 <= value <= 65535:
        output.error(f"{flag} {value} is not a port (1-65535)", exception=typer.Exit(code=1))


@example(
    "Move fm off a port something else already owns",
    "--http 8080 --https 8443",
    detail="Run this before the first install on a host whose 80/443 are taken: it writes the setting without creating or starting anything.",
)
@example(
    "Keep the origin private behind a local front",
    "--http 8080 --https 8443 --bind 127.0.0.1",
    detail="Only the front can then reach fm, so a forged X-Forwarded-Proto cannot arrive from anywhere else.",
)
@example(
    "Go back to the standard ports",
    "--http 80 --https 443",
)
def ports(
    ctx: typer.Context,
    http: Annotated[
        int,
        typer.Option("--http", help="Host port published to the proxy's :80.", show_default=False),
    ],
    https: Annotated[
        int,
        typer.Option("--https", help="Host port published to the proxy's :443.", show_default=False),
    ],
    bind: Annotated[
        str | None,
        typer.Option(
            "--bind",
            help="Host address to publish on, e.g. 127.0.0.1 to accept only a local front. Absent publishes on every interface.",
            show_default=False,
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Apply to a running stack without asking; the proxy is recreated."),
    ] = False,
):
    """
    Publish the global proxy on different host ports, so fm can share a machine with another web server.

    Only the HOST side moves: the proxy keeps listening on 80 and 443 inside its container, because every bench resolves its own domains to that address and a site's server-side calls to itself would otherwise stop working. Redirects fm writes pick the new port up from one generated file.

    On a host with no services yet this writes the setting and exits, creating nothing -- that is what makes it usable on a machine whose first install cannot get past a busy port. Where the stack already exists the ports are applied and the proxy is recreated, which is a brief outage for every bench on the host.

    Let's Encrypt HTTP-01 needs port 80 reachable at the public name, so moving off 80 means using --challenge dns01, --dev or --custom for certificates fm issues.
    """
    output = get_global_output_handler()

    _validate_port(output, "--http", http)
    _validate_port(output, "--https", https)
    if http == https:
        output.error("--http and --https cannot be the same port", exception=typer.Exit(code=1))

    fm_config = FMConfigManager.import_from_toml()
    fm_config.proxy.http_port = http
    fm_config.proxy.https_port = https
    fm_config.proxy.bind = bind
    fm_config.export_to_toml()

    where = f"{bind}:" if bind else ""
    output.print(f"Proxy publishes on {where}{http} (http) and {where}{https} (https)")

    services = ctx.obj.get("services") if ctx.obj else None
    if services is None or not services.compose_path.exists():
        # No stack yet: writing the setting is the whole job. This command is exempt from the
        # first-install path precisely so it can run here -- on a host whose install fails on a
        # busy port, creating the stack to change the port would fail for the port being busy.
        output.print("No global services yet; the next command creates them on these ports.")
        return

    if not services.apply_proxy_ports():
        output.print("Already published there; nothing to apply.")
        return

    if not yes:
        output.warning("Applying this recreates the global proxy: every bench on this host is briefly unreachable.")
        choice = output.prompt_ask(
            prompt="Recreate the proxy now? (default: no)",
            choices=["yes", "no"],
            default="no",
            required_flag="--yes or -y",
        )
        if choice != "yes":
            output.print("Saved; run 'fm services restart nginx-proxy --recreate' to apply it.", emoji_code="")
            services.compose_file_manager.write_to_file()
            return

    services.compose_file_manager.write_to_file()
    services.set_forwarded_trust_conf()
    # Recreate, not restart: a published port is fixed when the container is created.
    services.docker_client.compose.up(services=["nginx-proxy"], detach=True, force_recreate=True, stream=False)
    output.print("Proxy recreated on the new ports")

    if http != 80:
        output.print(
            "Port 80 is no longer fm's, so Let's Encrypt HTTP-01 cannot reach it: use --challenge dns01, --dev or --custom.",
            emoji_code="",
        )
