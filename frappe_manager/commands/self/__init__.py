"""Self subcommands for operations related to fm itself."""

import typer
from typer_examples import install

from frappe_manager.commands.self.compose import compose
from frappe_manager.commands.self.update import update
from frappe_manager.commands.self.update_images import update_images

self_app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)

# Activate typer-examples for this Typer app
install(self_app)

self_app.command()(update)
self_app.command(name="update-images")(update_images)
self_app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(compose)
