"""Services subcommands for managing global services."""

import typer

from frappe_manager.commands.services.restart import restart_services
from frappe_manager.commands.services.shell import shell_services
from frappe_manager.commands.services.start import start_services
from frappe_manager.commands.services.stop import stop_services

services_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

services_app.command(name="start", no_args_is_help=True)(start_services)
services_app.command(name="stop", no_args_is_help=True)(stop_services)
services_app.command(name="restart", no_args_is_help=True)(restart_services)
services_app.command(name="shell", no_args_is_help=True)(shell_services)
