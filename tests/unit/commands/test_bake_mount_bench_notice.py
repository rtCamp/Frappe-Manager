"""`fm bake` on a MOUNT bench builds an image `fm switch` can never deploy.

Each command guards only its own opposite case: bake refuses an image-runtime bench (nothing on
disk to bake FROM), switch refuses a mount bench (runtime is fixed at create time). Nobody guarded
the combination, so baking a mount bench spent four minutes and then failed at `fm switch` in a
second, while `fm bake --help` stated the opposite as fact.

Not a refusal: the image is a valid seed for a NEW bench via `fm create --runtime image`, which is
a real workflow. So the contract is two messages -- one before the build, which is the only moment
the minutes can still be saved, and one after, which is the only moment the tag exists.
"""

import sys
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.bake import bake
from frappe_manager.site_manager.modules.bake import BakeManager

bake_module = sys.modules["frappe_manager.commands.bake"]
runner = CliRunner()

BENCH = "mybench"

_CONFIG = """\
name = "mybench"
developer_mode = true
admin_tools = true
environment = "dev"
runtime = "{runtime}"
image = "local/mybench"
"""


@pytest.fixture
def cli():
    app = typer.Typer()
    app.command("bake")(bake)
    return app


@pytest.fixture
def world(tmp_path, monkeypatch):
    def _make(runtime: str):
        (tmp_path / BENCH).mkdir(parents=True, exist_ok=True)
        (tmp_path / BENCH / "bench_config.toml").write_text(_CONFIG.format(runtime=runtime))
        return tmp_path

    monkeypatch.setattr(bake_module, "CLI_BENCHES_DIRECTORY", tmp_path)
    monkeypatch.setattr(bake_module, "sitename_callback", lambda name: name)
    monkeypatch.setattr(BakeManager, "bake", lambda self, tag=None, push=None, nginx_tag=None: "local/mybench:t1")
    monkeypatch.setattr("frappe_manager.site_manager.modules.bake.DockerClient", MagicMock())
    return _make


def _flat(text: str) -> str:
    """Rich hard-wraps to the console width, so an assertion string that happens to span a wrap
    point fails on formatting rather than content."""
    return " ".join(text.split())


def test_a_mount_bench_is_warned_before_the_build_starts(cli, world):
    """The only message that can save the four minutes; it cannot name the tag, which does not
    exist yet."""
    world("mount")

    result = runner.invoke(cli, [BENCH])

    assert result.exit_code == 0, result.output
    before_build = _flat(result.output.split("Baked image")[0])
    assert "will refuse this image" in before_build


def test_a_mount_bench_is_given_the_command_that_does_work(cli, world):
    """Repeated on purpose: the pre-build warning could not carry the tag, and a constraint stated
    four minutes ago has scrolled away by the time the operator needs a command."""
    world("mount")

    result = runner.invoke(cli, [BENCH])

    after_build = _flat(result.output.split("Baked image")[-1])
    assert "fm create NAME --runtime image --app-image" in after_build
    assert "local/mybench:t1" in after_build


def test_an_image_bench_is_still_refused_outright(cli, world):
    """Unchanged: an image bench has no editable workspace to bake from, so it refuses before any
    build work rather than warning."""
    world("image")

    result = runner.invoke(cli, [BENCH])

    assert result.exit_code == 1
    assert "no editable workspace to bake from" in _flat(result.output)
    assert "will refuse this image" not in _flat(result.output)


def test_a_standalone_bake_says_nothing_about_runtimes(cli, world, monkeypatch):
    """A standalone bake has no bench at all, so neither message applies."""
    result = runner.invoke(cli, ["--apps", "frappe:version-15", "--app-image", "local/x"])

    assert result.exit_code == 0, result.output
    assert "mount runtime" not in _flat(result.output)
    assert "will refuse this image" not in _flat(result.output)
