"""
Shared fixtures for output_manager tests.

This module provides common fixtures used across multiple test files.
"""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_richprint(mocker):
    """Mock the richprint singleton for testing RichOutputHandler."""
    mock_rich = MagicMock()
    mocker.patch('frappe_manager.display_manager.DisplayManager.richprint', mock_rich)
    return mock_rich
