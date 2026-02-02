"""Helper functions for SSL commands."""

from typing import Optional
import typer
from frappe_manager.output_manager import OutputHandler, get_global_output_handler


def get_output_handler(ctx: typer.Context, context: Optional[object] = None) -> OutputHandler:
    return get_global_output_handler()
