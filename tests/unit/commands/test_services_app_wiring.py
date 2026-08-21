"""`fm services <sub>` with no arguments teaches instead of scolding.

Every services subcommand takes a required SERVICE_NAME whose allowed values a
user cannot guess. The sub-app therefore wires each subcommand with
``no_args_is_help=True``, so a bare ``fm services start`` renders that command's
own help -- the argument, its allowed service names and the worked examples --
rather than click's bare "Missing argument 'SERVICE_NAME'" usage error.

This file defends that wiring decision: the discoverable-help behaviour is the
contract, and dropping the flag would silently swap it for an error message.
"""

import pytest
from typer.testing import CliRunner

from frappe_manager.commands.services import services_app

runner = CliRunner()

SUBCOMMANDS = ["start", "stop", "restart", "shell"]


@pytest.mark.timeout(15)
@pytest.mark.parametrize("subcommand", SUBCOMMANDS)
def test_bare_subcommand_renders_help_instead_of_missing_argument_error(subcommand):
    result = runner.invoke(services_app, [subcommand])
    output = " ".join(result.output.split())

    # click's parse error for the omitted required argument must NOT be what
    # the user gets; the help screen is shown in its place.
    assert "Missing argument" not in output
    # ...and the help screen really is rendered: the enumerated service names
    # and the worked-examples panel only appear in the help panels.
    assert "Examples" in output
    assert "global-nginx-proxy" in output


@pytest.mark.timeout(15)
@pytest.mark.parametrize("subcommand", SUBCOMMANDS)
def test_bare_subcommand_does_not_run_the_command_body(subcommand):
    # Help-instead-of-error still short-circuits: nothing is dispatched, so a
    # missing ctx.obj (no root callback here) can never be touched.
    result = runner.invoke(services_app, [subcommand])

    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.exit_code != 0


@pytest.mark.timeout(15)
@pytest.mark.parametrize("subcommand", SUBCOMMANDS)
def test_subcommand_with_an_argument_is_not_diverted_to_help(subcommand):
    # The help divert is keyed on "no arguments" only: once an argument is
    # supplied the command parses and dispatches for real (and then fails on
    # the absent ctx.obj, proving the body was entered).
    result = runner.invoke(services_app, [subcommand, "global-db"])

    assert "Examples" not in result.output
    assert isinstance(result.exception, TypeError)
