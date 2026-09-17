"""`fm list` output-form flags: --json and --paths are two exclusive shapes of stdout.

The json branch returns without ever reading --paths, so combining them used to silently
prefer --json. Refusing beats preferring: the operator asked for two shapes at once.
"""

from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.list import list as list_command
from frappe_manager.output_manager import get_global_output_handler

runner = CliRunner()


@pytest.fixture
def cli():
    test_app = typer.Typer()
    test_app.command("list")(list_command)
    return test_app


def _invoke(cli, args, obj_extra: dict | None = None):
    obj = {"services": MagicMock(), "verbose": False}
    obj.update(obj_extra or {})
    with patch("frappe_manager.commands.list.BenchService") as service_cls:
        service_cls.return_value.list_benches_data.return_value = []
        result = runner.invoke(cli, args, obj=obj)
    return result, service_cls


def test_json_with_paths_is_refused(cli):
    handler = get_global_output_handler()
    with patch.object(handler, "display_error") as display_error:
        result, service_cls = _invoke(cli, ["--json", "--paths"])

    assert result.exit_code == 1
    assert "--paths cannot be combined with --json" in " ".join(
        str(c.args[0]) for c in display_error.call_args_list
    )
    service_cls.return_value.list_benches_data.assert_not_called()


def test_global_json_mode_with_paths_is_refused(cli):
    handler = get_global_output_handler()
    with patch.object(handler, "display_error"):
        result, service_cls = _invoke(cli, ["--paths"], obj_extra={"json": True})

    assert result.exit_code == 1
    service_cls.return_value.list_benches_data.assert_not_called()


def test_each_form_alone_still_works(cli):
    result_json, _ = _invoke(cli, ["--json"])
    assert result_json.exit_code == 0

    handler = get_global_output_handler()
    with patch.object(handler, "stop"):
        result_paths, _ = _invoke(cli, ["--paths"])
    assert result_paths.exit_code == 0
