"""Regression contracts for the two helpers `fm self update` and `fm self update-images` stand on.

* `install_package` must use the installer this fm was actually installed with. fm's own
  installer (scripts/install.sh) uses `uv tool install`, and a uv tool venv is not pip-seeded, so
  `python -m pip install` cannot work there -- it exits non-zero and the update path fails
  end to end with a generic error.
* `pull_docker_images` must attempt every image and report the aggregate. `OutputHandler.error()`
  always re-raises, so using it inside the loop aborted on the first failure and made both the
  `no_error` return value and its callers' cleanup unreachable.

No docker daemon, no network and no real installer is invoked: both seams are mocked.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.docker.docker_exceptions import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.helpers import build_install_package_command, install_package
from frappe_manager.utils.site import pull_docker_images

HELPERS = "frappe_manager.utils.helpers"
UV_TOOL_PYTHON = "/Users/x/.local/share/uv/tools/frappe-manager/bin/python"


def find_spec_without_pip(name, *args, **kwargs):
    if name == "pip":
        return None
    return MagicMock(name=f"spec:{name}")


# =========================================================================== #
# install_package
# =========================================================================== #


def test_an_interpreter_without_pip_installs_through_uv_tool():
    """D59: `python -m pip install` cannot run in a uv tool venv, so the documented upgrade path
    (`fm self update`) could never install anything on a standard installation."""
    with (
        patch(f"{HELPERS}.importlib.util.find_spec", side_effect=find_spec_without_pip),
        patch(f"{HELPERS}.shutil.which", return_value="/opt/homebrew/bin/uv"),
    ):
        argv = build_install_package_command("frappe-manager", "0.19.3")

    assert argv == ["/opt/homebrew/bin/uv", "tool", "install", "--force", "frappe-manager==0.19.3"]


def test_an_interpreter_inside_a_uv_tools_tree_installs_through_uv_tool():
    """The uv tool venv is the installer's own layout; detect it by path too, so fm never
    reinstalls itself into a venv uv does not track."""
    with (
        patch(f"{HELPERS}.sys.executable", UV_TOOL_PYTHON),
        patch(f"{HELPERS}.shutil.which", return_value="/opt/homebrew/bin/uv"),
    ):
        argv = build_install_package_command("frappe-manager", "0.19.3")

    assert argv[:4] == ["/opt/homebrew/bin/uv", "tool", "install", "--force"]


def test_uv_tool_dir_from_the_environment_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path))
    with (
        patch(f"{HELPERS}.sys.executable", str(tmp_path / "frappe-manager" / "bin" / "python")),
        patch(f"{HELPERS}.shutil.which", return_value="/opt/homebrew/bin/uv"),
    ):
        argv = build_install_package_command("frappe-manager", "0.19.3")

    assert argv[1:3] == ["tool", "install"]


def test_a_pip_seeded_interpreter_still_uses_pip():
    """A pip install (`pipx`, a plain venv) must keep working exactly as before."""
    with (
        patch(f"{HELPERS}.importlib.util.find_spec", return_value=MagicMock()),
        patch(f"{HELPERS}.shutil.which", return_value=None),
    ):
        argv = build_install_package_command("frappe-manager", "0.19.3")

    assert argv == [sys.executable, "-m", "pip", "install", "frappe-manager==0.19.3"]


def test_neither_installer_available_names_the_manual_command():
    with (
        patch(f"{HELPERS}.importlib.util.find_spec", side_effect=find_spec_without_pip),
        patch(f"{HELPERS}.shutil.which", return_value=None),
        pytest.raises(FrappeManagerException) as excinfo,
    ):
        build_install_package_command("frappe-manager", "0.19.3")

    assert "uv tool install --force frappe-manager==0.19.3" in str(excinfo.value)


def test_install_package_runs_the_resolved_command():
    handler = get_global_output_handler()
    with (
        patch(f"{HELPERS}.build_install_package_command", return_value=["uv", "tool", "install"]) as build,
        patch(f"{HELPERS}.run_command_with_exit_code", return_value=iter([])) as run,
        patch.object(handler, "live_lines"),
    ):
        install_package("frappe-manager", "0.19.3")

    build.assert_called_once_with("frappe-manager", "0.19.3")
    assert run.call_args.args[0] == ["uv", "tool", "install"]


# =========================================================================== #
# pull_docker_images
# =========================================================================== #


def test_every_image_is_attempted_even_after_a_failure():
    """D61: `output.error()` always re-raises, so the loop aborted on the first image -- a single
    rate-limited pull skipped every remaining image and made `return no_error` unreachable."""
    attempted: list[str] = []

    def pull(container_name, stream):
        attempted.append(container_name)
        if container_name == "Aimg:1":
            raise DockerException(
                ["docker", "pull", container_name],
                SubprocessOutput(stdout=[], stderr=["toomanyrequests"], combined=[], exit_code=1),
            )
        return iter([])

    docker = MagicMock(name="docker_client")
    docker.pull.side_effect = pull
    handler = get_global_output_handler()

    with (
        patch("frappe_manager.docker.DockerClient", return_value=docker),
        patch(
            "frappe_manager.utils.site.get_all_docker_images",
            return_value={
                "a": {"name": "Aimg", "tag": "1"},
                "b": {"name": "Bimg", "tag": "2"},
            },
        ),
        patch.object(handler, "live_lines"),
        patch.object(handler, "change_head"),
        patch.object(handler, "print") as printed,
        patch.object(handler, "display_error") as errored,
    ):
        result = pull_docker_images()

    assert attempted == ["Aimg:1", "Bimg:2"]
    assert result is False
    # The failed image must not also be announced as pulled.
    reported = SimpleNamespace(
        pulled="\n".join(c.args[0] for c in printed.call_args_list),
        failed="\n".join(c.args[0] for c in errored.call_args_list),
    )
    assert "Bimg:2" in reported.pulled
    assert "Aimg:1" not in reported.pulled
    assert "Failed to pull Aimg:1" in reported.failed


def test_all_images_pulled_reports_success():
    docker = MagicMock(name="docker_client")
    docker.pull.return_value = iter([])
    handler = get_global_output_handler()

    with (
        patch("frappe_manager.docker.DockerClient", return_value=docker),
        patch(
            "frappe_manager.utils.site.get_all_docker_images",
            return_value={"a": {"name": "Aimg", "tag": "1"}},
        ),
        patch.object(handler, "live_lines"),
        patch.object(handler, "change_head"),
        patch.object(handler, "print"),
    ):
        assert pull_docker_images() is True
