"""`fm ssl` sub-app wiring: a discoverable group and an opaque escape hatch.

Two wiring decisions in ``frappe_manager/commands/ssl/__init__.py`` carry user
visible behaviour:

* the ssl Typer group is built with ``no_args_is_help=True``, so a bare
  ``fm ssl`` lists its subcommands instead of failing with "Missing command.";
* ``ssl acme-sh`` is registered with ``allow_extra_args`` /
  ``ignore_unknown_options``, so arbitrary acme.sh flags reach the real acme.sh
  binary verbatim instead of being rejected by click as unknown options.

Both are defended here by observable behaviour: what the group prints with no
arguments, and the exact argv handed to the acme.sh subprocess helper.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from frappe_manager.commands.ssl import ssl_app

runner = CliRunner()


@pytest.mark.timeout(15)
def test_bare_ssl_group_lists_subcommands_instead_of_erroring():
    result = runner.invoke(ssl_app, [])
    output = " ".join(result.output.split())

    assert "Missing command." not in output
    # The command index is the whole point of the help divert.
    for name in ("renew", "list", "add", "remove", "acme-sh", "dns-config"):
        assert name in output


@pytest.fixture
def acmesh(tmp_path):
    """An installed-looking acme.sh plus the services object the command reads."""
    ssl_dir = tmp_path / "ssl"
    home = ssl_dir / "acmesh" / ".acme.sh"
    home.mkdir(parents=True)
    binary = home / "acme.sh"
    binary.write_text("#!/bin/sh\nexit 0\n")

    services = MagicMock()
    services.proxy_storage.dirs.ssl.host = ssl_dir
    return binary, home, services


@pytest.mark.timeout(15)
def test_acme_sh_forwards_unknown_options_and_extra_args_verbatim(acmesh):
    binary, home, services = acmesh

    with patch("frappe_manager.commands.ssl.acme_sh.stream_command_output") as stream:
        stream.return_value = iter([("exit_code", b"0")])
        result = runner.invoke(
            ssl_app,
            ["acme-sh", "--info", "-d", "example.com", "--force", "leftover"],
            obj={"services": services},
        )

    # Neither the unknown options nor the bare extra argument may be rejected.
    assert result.exit_code == 0, result.output
    stream.assert_called_once()
    cmd = stream.call_args.args[0]
    assert cmd == [
        str(binary),
        "--home",
        str(home),
        "--info",
        "-d",
        "example.com",
        "--force",
        "leftover",
    ]


@pytest.mark.timeout(15)
def test_acme_sh_without_args_asks_acme_sh_for_help(acmesh):
    binary, home, services = acmesh

    with patch("frappe_manager.commands.ssl.acme_sh.stream_command_output") as stream:
        stream.return_value = iter([("exit_code", b"0")])
        result = runner.invoke(ssl_app, ["acme-sh"], obj={"services": services})

    assert result.exit_code == 0, result.output
    assert stream.call_args.args[0] == [str(binary), "--home", str(home), "--help"]


@pytest.mark.timeout(15)
def test_acme_sh_propagates_the_acme_sh_exit_code(acmesh):
    _, _, services = acmesh

    with patch("frappe_manager.commands.ssl.acme_sh.stream_command_output") as stream:
        stream.return_value = iter([("exit_code", b"7")])
        result = runner.invoke(ssl_app, ["acme-sh", "--renew"], obj={"services": services})

    assert result.exit_code == 7
