"""Self subcommands for operations related to fm itself."""

import typer
from frappe_manager.commands.self.update import update
from frappe_manager.commands.self.update_images import update_images
from frappe_manager.commands.self.compose import compose

self_app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)

self_app.command()(update)
self_app.command(name='update-images')(update_images)
self_app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(compose)
