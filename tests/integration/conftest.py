"""
Shared pytest fixtures for integration tests.

This module provides fixtures that are automatically available to all integration tests.
"""

import pytest

from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.rich_output import RichOutputHandler


@pytest.fixture(autouse=True)
def init_global_output_handler():
    """
    Initialize global output handler for all tests.

    This fixture runs automatically before every test to ensure the global
    output handler is initialized, which is required by app_callback() and
    other functions that use get_global_output_handler().

    After each test, the handler is reset to None to ensure test isolation.
    """
    # Initialize with a basic RichOutputHandler (no logging)
    handler = RichOutputHandler()
    set_global_output_handler(handler)

    yield

    # Reset after test to ensure isolation
    set_global_output_handler(None)
