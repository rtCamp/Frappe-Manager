"""
Tests for stream separation functionality.

Ensures that data output goes to stdout and status output goes to stderr,
enabling proper Unix piping (e.g., fm list | jq).
"""

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.table import Table

from frappe_manager.output_manager.flags import OutputRefactoringFlags
from frappe_manager.output_manager.rich_output import RichOutputHandler


class TestStreamSeparation:
    """Test stream separation between stdout (data) and stderr (status)."""

    def test_print_data_uses_stdout_in_transition_mode(self):
        output = RichOutputHandler()

        with patch.object(output._richprint.stdout, 'print') as mock_stdout:
            with patch.dict('os.environ', {'FM_STREAM_SEPARATION': 'transition'}):
                output.print_data("test data")

                mock_stdout.assert_called_once()

    def test_print_data_uses_stderr_in_legacy_mode(self):
        output = RichOutputHandler()

        with patch.object(output._richprint.stderr, 'print') as mock_stderr:
            with patch.dict('os.environ', {'FM_STREAM_SEPARATION': 'legacy'}):
                output.print_data("test data")

                mock_stderr.assert_called_once()

    def test_print_status_always_uses_stderr(self):
        output = RichOutputHandler()

        with patch.object(output._richprint.stderr, 'print') as mock_stderr:
            output.print_status("Working...")

            mock_stderr.assert_called_once()
            args = mock_stderr.call_args[0]
            assert "Working..." in args[0]

    def test_print_data_handles_rich_table(self):
        output = RichOutputHandler()
        table = Table()
        table.add_column("Name")
        table.add_row("test")

        with patch.object(output._richprint.stdout, 'print') as mock_stdout:
            with patch.dict('os.environ', {'FM_STREAM_SEPARATION': 'transition'}):
                output.print_data(table)

                mock_stdout.assert_called_once()
                assert isinstance(mock_stdout.call_args[0][0], Table)

    def test_print_data_serializes_dict_to_json(self):
        output = RichOutputHandler()
        data = {"key": "value", "number": 42}

        with patch.object(output._richprint.stdout, 'print') as mock_stdout:
            with patch.dict('os.environ', {'FM_STREAM_SEPARATION': 'transition'}):
                output.print_data(data)

                mock_stdout.assert_called_once()
                printed_text = mock_stdout.call_args[0][0]
                parsed = json.loads(printed_text)
                assert parsed == data

    def test_print_data_serializes_list_to_json(self):
        output = RichOutputHandler()
        data = ["item1", "item2", "item3"]

        with patch.object(output._richprint.stdout, 'print') as mock_stdout:
            with patch.dict('os.environ', {'FM_STREAM_SEPARATION': 'transition'}):
                output.print_data(data)

                mock_stdout.assert_called_once()
                printed_text = mock_stdout.call_args[0][0]
                parsed = json.loads(printed_text)
                assert parsed == data


class TestDisplayManagerStreamSeparation:
    """Test DisplayManager.live_lines stream separation."""

    def test_live_lines_routes_stdout_correctly_non_tty(self):
        from frappe_manager.display_manager.DisplayManager import DisplayManager

        dm = DisplayManager()
        dm._is_tty = False

        data = iter(
            [
                ("stdout", b"data line 1"),
                ("stdout", b"data line 2"),
            ]
        )

        with patch.object(dm.stdout, 'print') as mock_stdout:
            with patch.object(dm.stderr, 'print') as mock_stderr:
                dm.live_lines(data, stdout=True, stderr=False)

                assert mock_stdout.call_count == 2
                assert mock_stderr.call_count == 0

    def test_live_lines_routes_stderr_correctly_non_tty(self):
        from frappe_manager.display_manager.DisplayManager import DisplayManager

        dm = DisplayManager()
        dm._is_tty = False

        data = iter(
            [
                ("stderr", b"error line 1"),
                ("stderr", b"error line 2"),
            ]
        )

        with patch.object(dm.stdout, 'print') as mock_stdout:
            with patch.object(dm.stderr, 'print') as mock_stderr:
                dm.live_lines(data, stdout=False, stderr=True)

                assert mock_stdout.call_count == 0
                assert mock_stderr.call_count == 2

    def test_live_lines_routes_mixed_streams_correctly_non_tty(self):
        from frappe_manager.display_manager.DisplayManager import DisplayManager

        dm = DisplayManager()
        dm._is_tty = False

        data = iter(
            [
                ("stdout", b"data line"),
                ("stderr", b"error line"),
                ("stdout", b"another data line"),
            ]
        )

        with patch.object(dm.stdout, 'print') as mock_stdout:
            with patch.object(dm.stderr, 'print') as mock_stderr:
                dm.live_lines(data, stdout=True, stderr=True)

                assert mock_stdout.call_count == 2
                assert mock_stderr.call_count == 1

    def test_live_lines_respects_stdout_flag(self):
        from frappe_manager.display_manager.DisplayManager import DisplayManager

        dm = DisplayManager()
        dm._is_tty = False

        data = iter(
            [
                ("stdout", b"data line"),
                ("stderr", b"error line"),
            ]
        )

        with patch.object(dm.stdout, 'print') as mock_stdout:
            with patch.object(dm.stderr, 'print') as mock_stderr:
                dm.live_lines(data, stdout=False, stderr=True)

                assert mock_stdout.call_count == 0
                assert mock_stderr.call_count == 1

    def test_live_lines_respects_stderr_flag(self):
        from frappe_manager.display_manager.DisplayManager import DisplayManager

        dm = DisplayManager()
        dm._is_tty = False

        data = iter(
            [
                ("stdout", b"data line"),
                ("stderr", b"error line"),
            ]
        )

        with patch.object(dm.stdout, 'print') as mock_stdout:
            with patch.object(dm.stderr, 'print') as mock_stderr:
                dm.live_lines(data, stdout=True, stderr=False)

                assert mock_stdout.call_count == 1
                assert mock_stderr.call_count == 0
