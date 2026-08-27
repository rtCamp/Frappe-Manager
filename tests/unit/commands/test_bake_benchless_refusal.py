"""`fm bake` with no bench name and no `--apps`/`--config` is a usage error.

`bake` has two modes, and the benchname help says so: with a bench it bakes that bench's apps,
without one it needs `--apps` or a `--config` to know what to build. The third shape -- nothing at
all -- used to fall through to the bench branch and hand `None` to `sitename_callback`, whose
interactive picker would then offer to bake whatever bench it landed on. That is the same trap
`arguments.py` documents `RequiredBenchNameArgument` as existing to prevent for `fm prune` and
`fm switch`: a destructive-ish command silently choosing its own target.

So the refusal is the contract: nonzero exit, the standalone-bake usage message, and
`sitename_callback` never reached.
"""

from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.bake import bake
from frappe_manager.site_manager.modules.bake import BakeManager

# `frappe_manager.commands` re-exports the `bake` FUNCTION under the same name, shadowing the
# submodule attribute, so the module itself has to come from the import system.
bake_cmd = import_module("frappe_manager.commands.bake")

runner = CliRunner()


@pytest.fixture
def cli():
    test_app = typer.Typer()
    test_app.command("bake")(bake)
    return test_app


@pytest.fixture
def wired(monkeypatch):
    """Nothing here may reach a bench picker, a bench directory or a build."""
    picker = MagicMock(name="sitename_callback", return_value="picked.localhost")
    baked = MagicMock(name="bake", return_value="ghcr.io/acme/mysite:v1")
    monkeypatch.setattr(bake_cmd, "sitename_callback", picker)
    monkeypatch.setattr(BakeManager, "bake", baked)
    monkeypatch.setattr("frappe_manager.site_manager.modules.bake.DockerClient", MagicMock())
    return picker, baked


def test_no_bench_and_no_apps_or_config_is_refused_instead_of_prompting(cli, wired):
    picker, baked = wired

    result = runner.invoke(cli, [])

    assert result.exit_code != 0
    assert "Standalone bake needs apps" in result.output
    picker.assert_not_called()
    baked.assert_not_called()


def test_the_refusal_survives_options_that_do_not_name_what_to_build(cli, wired):
    """`--image` says where the result goes, not what goes in it, so it is not a standalone spec."""
    picker, baked = wired

    result = runner.invoke(cli, ["--image", "ghcr.io/acme/mysite:v1", "--push"])

    assert result.exit_code != 0
    assert "Standalone bake needs apps" in result.output
    picker.assert_not_called()
    baked.assert_not_called()


def test_a_bench_name_still_resolves_through_the_callback(cli, wired):
    """The guard is scoped to the bench-less shape: a named bench must still be validated."""
    picker, _ = wired

    with patch.object(bake_cmd, "apply_config_overlays"):
        runner.invoke(cli, ["some.localhost"])

    picker.assert_called_once_with("some.localhost")


def test_apps_alone_still_bakes_without_a_bench(cli, wired):
    """The other half of the guard: --apps IS a standalone spec and must not be refused."""
    _, baked = wired

    result = runner.invoke(cli, ["--apps", "frappe:version-15", "--image", "ghcr.io/acme/mysite:v1"])

    assert result.exit_code == 0, result.output
    baked.assert_called_once()
