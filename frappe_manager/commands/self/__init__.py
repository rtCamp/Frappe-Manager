"""Self subcommands for operations related to fm itself."""

import typer
from typer_examples import install

from frappe_manager.commands.self.stop import stop
from frappe_manager.commands.self.uninstall import uninstall
from frappe_manager.commands.self.update_images import update_images
from frappe_manager.commands.self.upgrade import upgrade

self_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(self_app)

self_app.command()(upgrade)
self_app.command(name="update-images")(update_images)
self_app.command()(stop)
self_app.command()(uninstall)
