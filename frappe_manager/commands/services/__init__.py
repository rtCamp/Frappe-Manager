"""Services subcommands for the global stack shared by every bench."""

import typer
from typer_examples import install

from frappe_manager.commands.services.info import info as info_services
from frappe_manager.commands.services.migrate import migrate_services
from frappe_manager.commands.services.ports import ports as ports_services
from frappe_manager.commands.services.prune import prune_services
from frappe_manager.commands.services.restart import restart_services
from frappe_manager.commands.services.shell import shell_services
from frappe_manager.commands.services.start import start_services
from frappe_manager.commands.services.stop import stop_services
from frappe_manager.commands.services.trusted_proxies import (
    clear as clear_trusted_proxies,
)
from frappe_manager.commands.services.trusted_proxies import (
    set_trusted,
    trusted_proxies_app,
)
from frappe_manager.commands.services.trusted_proxies import (
    show as show_trusted_proxies,
)

services_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(services_app)

services_app.command(name="info")(info_services)
services_app.command(name="migrate")(migrate_services)
services_app.command(name="start", no_args_is_help=True)(start_services)
services_app.command(name="stop", no_args_is_help=True)(stop_services)
services_app.command(name="restart", no_args_is_help=True)(restart_services)
services_app.command(name="shell", no_args_is_help=True)(shell_services)
services_app.command(name="ports", no_args_is_help=True)(ports_services)
services_app.add_typer(trusted_proxies_app, name="trusted-proxies", help="Which proxies in front of fm may speak for the client.")
trusted_proxies_app.command(name="show")(show_trusted_proxies)
trusted_proxies_app.command(name="set", no_args_is_help=True)(set_trusted)
trusted_proxies_app.command(name="clear")(clear_trusted_proxies)
services_app.command(name="prune")(prune_services)
