"""
Output abstraction layer for Frappe Manager.

This module provides an abstract interface for handling output in business logic,
allowing the CLI to be decoupled from the core modules. This enables future support
for alternative interfaces (API, WebSocket, etc.) without rewriting business logic.
"""

from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.json_output import JSONOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler

__all__ = [
    "JSONOutputHandler",
    "OutputHandler",
    "RichOutputHandler",
    "SilentOutputHandler",
]
