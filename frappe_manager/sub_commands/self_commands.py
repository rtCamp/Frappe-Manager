import typer
from frappe_manager.sub_commands.update_command import update_app

self_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
self_app.add_typer(update_app, name="update", help="Check for updates, Update images etc.")
