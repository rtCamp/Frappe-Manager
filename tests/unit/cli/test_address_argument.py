"""The `BENCH[/SITE]` positional, black-box through the CLI.

A bench holds exactly one site today, so `fm shell` is the only command that can do
anything with a site part and every other command refuses one. These tests pin that
split, and they pin it at the CLI boundary rather than by calling the callbacks,
because the exit code and the message are the contract a user meets.

Most cases need no bench on disk: the refusal happens in the parameter callback,
BEFORE the must-exist check, which is the property that makes a mistyped address
cheap to diagnose. The two that need a bench say so.
"""

from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.create import create
from frappe_manager.commands.restart import restart
from frappe_manager.commands.shell import shell
from frappe_manager.commands.ssl.list import list_certificates

runner = CliRunner()

BENCH = "x.localhost"


@pytest.fixture
def benches(tmp_path):
    """A benches directory holding one real bench, patched into both callback modules."""
    root = tmp_path / "sites"
    (root / BENCH).mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        yield root


def _app(name, fn):
    app = typer.Typer()
    app.command(name)(fn)
    return app


# --------------------------------------------------------------- parse failures


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("a/b/c", "more than one"),
        ("shop/", "empty site"),
        ("/shop", "empty bench"),
    ],
)
def test_a_malformed_address_is_refused_before_any_bench_lookup(benches, address, expected):
    """No bench named `a` exists, and the message is about the ADDRESS, not a missing bench:
    a parse failure must not be reported as `bench not found`."""
    result = runner.invoke(_app("restart", restart), [address])
    assert result.exit_code == 2
    assert expected in result.output
    assert "not found" not in result.output.lower()


# ------------------------------------------------- a site part where none is honoured


def test_a_bench_scoped_command_refuses_a_site_part(benches):
    result = runner.invoke(_app("restart", restart), [f"{BENCH}/{BENCH}"])
    assert result.exit_code == 2
    assert "takes a bench, not a site" in result.output


def test_the_refusal_names_the_bench_form_to_use(benches):
    """The operator needs the fix, not just the complaint."""
    result = runner.invoke(_app("restart", restart), [f"{BENCH}/{BENCH}"])
    assert f"use '{BENCH}'" in result.output


def test_an_ssl_command_refuses_a_site_part_too(benches):
    """The four `ssl` commands carried NO callback before this, so a slashed value reached
    `Bench.get_object` and died as a not-found error on a nested path."""
    result = runner.invoke(_app("list", list_certificates), [f"{BENCH}/{BENCH}"])
    assert result.exit_code == 2
    assert "takes a bench, not a site" in result.output


def test_a_bench_scoped_command_still_accepts_a_plain_bench(benches):
    """The refusal must not have cost the ordinary form: this gets past argument parsing
    and fails later on bench internals instead."""
    result = runner.invoke(_app("restart", restart), [BENCH])
    assert "takes a bench, not a site" not in result.output


# ------------------------------------------------------------------- fm create


def test_create_refuses_an_address():
    """`fm create` cannot add a site to a bench that does not exist yet."""
    result = runner.invoke(_app("create", create), ["shop/a.localhost"])
    assert result.exit_code == 2
    assert "not a bench/site address" in result.output


def test_create_refuses_the_reserved_name():
    """A bare `all` is to become the address meaning every bench. Reserved now so no bench
    can be created that the keyword would later collide with."""
    result = runner.invoke(_app("create", create), ["all"])
    assert result.exit_code == 2
    assert "'all' is reserved" in result.output


def test_the_reserved_name_is_refused_before_the_localhost_suffix_is_added():
    """`validate_sitename` would turn `all` into `all.localhost` and the check would miss."""
    result = runner.invoke(_app("create", create), ["all"])
    assert "all.localhost" not in result.output


def test_create_still_accepts_an_ordinary_name(tmp_path):
    root = tmp_path / "sites"
    root.mkdir(parents=True)
    with patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root):
        result = runner.invoke(_app("create", create), ["shop"])
    assert "not a bench/site address" not in result.output
    assert "is reserved" not in result.output


# -------------------------------------------------------------------- fm shell


def test_shell_accepts_the_benchs_own_site(benches):
    """The one command that honours a site part. It gets past parsing; the body then fails
    on docker, which is not what this asserts."""
    result = runner.invoke(_app("shell", shell), [f"{BENCH}/{BENCH}"])
    assert "takes a bench, not a site" not in result.output
    assert "has no site" not in result.output


def test_shell_refuses_a_site_the_bench_does_not_have(benches):
    """A bench holds exactly one site and its name is the bench's, so anything else is a
    typo worth catching before any container is touched."""
    result = runner.invoke(_app("shell", shell), [f"{BENCH}/nope.localhost"])
    assert result.exit_code == 2
    assert f"bench '{BENCH}' has no site 'nope.localhost'" in result.output


def test_shell_names_the_site_it_does_have(benches):
    result = runner.invoke(_app("shell", shell), [f"{BENCH}/nope.localhost"])
    assert f"its site is '{BENCH}'" in result.output


def test_shell_has_no_site_option():
    """`--site` was replaced by the address; leaving both would be two ways to say one thing."""
    result = runner.invoke(_app("shell", shell), ["--help"])
    assert "--site" not in result.output


def test_shell_help_documents_the_address():
    result = runner.invoke(_app("shell", shell), ["--help"])
    assert "bench/site" in result.output
