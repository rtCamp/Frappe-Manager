"""Helper functions for SSL commands."""


import typer

from frappe_manager.output_manager import OutputHandler, get_global_output_handler


def get_output_handler(ctx: typer.Context, context: object | None = None) -> OutputHandler:
    return get_global_output_handler()
