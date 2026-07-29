"""Flag-conflict guards for `fm restart` (drain-by-default UX).

The drain gate defaults to on; killing work requires explicit words. These
tests pin the conflict matrix: explicit `--drain` conflicts with `--force`
and `--service`, `--force` conflicts with `--rolling`, while `--force` or
`--service` alone silently imply no-drain and proceed past flag validation.
"""

from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.restart import restart

runner = CliRunner()


@pytest.fixture
def cli(tmp_path):
    """Isolated app around the real restart command.

    The bench directory exists on disk so the parse-time sitename callback
    passes and the body's flag guards are what gets exercised.
    """
    test_app = typer.Typer()
    test_app.command("restart")(restart)
    benches = tmp_path / "sites"
    (benches / "x.localhost").mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", benches):
        yield test_app


def test_force_with_explicit_drain_conflicts(cli):
    result = runner.invoke(cli, ["x.localhost", "--force", "--drain"])
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_force_with_rolling_conflicts(cli):
    result = runner.invoke(cli, ["x.localhost", "--force", "--rolling"])
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_service_with_explicit_drain_conflicts(cli):
    result = runner.invoke(cli, ["x.localhost", "--service", "frappe", "--drain"])
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_service_alone_passes_flag_validation(cli):
    # No literal --drain token: the default must not trigger the conflict
    # guard; the command fails later on bench internals instead.
    result = runner.invoke(cli, ["x.localhost", "--service", "frappe"])
    assert "cannot be combined" not in result.output
    assert result.exit_code != 0


def test_force_alone_passes_flag_validation(cli):
    result = runner.invoke(cli, ["x.localhost", "--force"])
    assert "cannot be combined" not in result.output
    assert result.exit_code != 0


def test_no_drain_with_force_is_allowed_past_guards(cli):
    # --no-drain is the explicit kill opt-out; combining it with --force is
    # not a contradiction and must not hit the conflict guard.
    result = runner.invoke(cli, ["x.localhost", "--force", "--no-drain"])
    assert "cannot be combined" not in result.output
