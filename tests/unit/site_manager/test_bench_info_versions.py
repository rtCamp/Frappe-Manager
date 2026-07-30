"""Bench info sources (no apps.json, no container exec/run):

- Python/Node versions: image runtime -> baked image labels
  (`fm.python.version`/`fm.node.version`); mount runtime -> uv/fnm symlinks.
- Apps: image runtime -> `fm.apps` label; mount runtime -> git under `apps/`.
"""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.utils.site import (
    read_bench_app_refs,
    read_bench_node_version,
    read_bench_python_version,
)


def _make_bench(tmp_path):
    fb = tmp_path / "workspace" / "frappe-bench"
    (fb / ".uv").mkdir(parents=True)
    (fb / ".uv" / "python-default").symlink_to("cpython-3.12.9-linux-x86_64-gnu")
    (fb / ".fnm" / "aliases").mkdir(parents=True)
    (fb / ".fnm" / "aliases" / "default").symlink_to("../node-versions/v22.11.0/installation")
    return fb


def _git_app(apps_dir, name, branch="version-15"):
    """Create apps/<name> as a git repo committed on <branch>."""
    path = apps_dir / name
    path.mkdir(parents=True)

    def g(*a):
        # -c commit.gpgsign=false: never depend on the developer's signing
        # setup (an ssh/gpg signer that prompts would hang or fail the test).
        subprocess.run(  # noqa: S603, S607
            ["git", "-C", str(path), "-c", "commit.gpgsign=false", *a], check=True, capture_output=True
        )

    g("init", "-q", "-b", branch)
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    (path / "readme").write_text("x")
    g("add", ".")
    g("commit", "-q", "-m", "init")
    return path


def _info(runtime, tmp_path, docker_client=None, tag="repo:tag"):
    info = object.__new__(BenchInfo)  # bypass __init__ (no services/docker setup)
    info.bench_path = tmp_path
    info.docker_client = docker_client
    info.bench_config = SimpleNamespace(
        runtime=runtime,
        deploy_state=SimpleNamespace(current_tag=tag) if tag else None,
    )
    return info


# --- versions ---


def test_read_versions_from_symlinks(tmp_path):
    fb = _make_bench(tmp_path)
    assert read_bench_python_version(fb) == "3.12.9"
    assert read_bench_node_version(fb) == "v22.11.0"


def test_read_versions_missing_returns_none(tmp_path):
    assert read_bench_python_version(tmp_path) is None
    assert read_bench_node_version(tmp_path) is None


def test_image_runtime_reads_version_labels(tmp_path):
    dc = MagicMock()
    dc.image_labels.return_value = {"fm.python.version": "3.14.2", "fm.node.version": "v24.11.0"}
    info = _info(BenchRuntime.image, tmp_path, docker_client=dc)
    assert info.get_python_version() == "3.14.2"
    assert info.get_node_version() == "v24.11.0"
    dc.image_labels.assert_called_with("repo:tag")


def test_image_runtime_missing_version_label_is_na(tmp_path):
    dc = MagicMock()
    dc.image_labels.return_value = {}
    info = _info(BenchRuntime.image, tmp_path, docker_client=dc)
    assert info.get_python_version() == "N/A"
    assert info.get_node_version() == "N/A"


def test_mount_runtime_reads_version_symlinks(tmp_path):
    _make_bench(tmp_path)
    info = _info(BenchRuntime.mount, tmp_path)
    assert info.get_python_version() == "3.12.9"
    assert info.get_node_version() == "v22.11.0"


# --- apps + refs ---


def test_read_app_refs_branch_and_commit(tmp_path):
    fb = tmp_path / "workspace" / "frappe-bench"
    apps = fb / "apps"
    _git_app(apps, "erpnext", branch="version-15")
    _git_app(apps, "frappe", branch="version-15")
    (fb / "sites").mkdir(parents=True)
    (fb / "sites" / "apps.txt").write_text("frappe\nerpnext\n")

    refs = read_bench_app_refs(fb)
    assert [a["name"] for a in refs] == ["frappe", "erpnext"]  # frappe first, apps.txt order
    frappe = refs[0]
    assert frappe["ref"] == "version-15"
    assert frappe["commit"]
    assert len(frappe["commit"]) >= 7  # short sha present


def test_read_app_refs_missing_dir_is_empty(tmp_path):
    assert read_bench_app_refs(tmp_path / "nope") == []


def test_get_bench_apps_image_reads_label(tmp_path):
    payload = [{"name": "frappe", "ref": "version-15", "commit": "abc1234"}]
    dc = MagicMock()
    dc.image_labels.return_value = {"fm.apps": json.dumps(payload)}
    info = _info(BenchRuntime.image, tmp_path, docker_client=dc)
    assert info.get_bench_apps() == payload


def test_get_bench_apps_image_no_label(tmp_path):
    dc = MagicMock()
    dc.image_labels.return_value = {}
    info = _info(BenchRuntime.image, tmp_path, docker_client=dc)
    assert info.get_bench_apps() == []


def test_get_bench_apps_mount_reads_git(tmp_path):
    fb = tmp_path / "workspace" / "frappe-bench"
    _git_app(fb / "apps", "frappe", branch="develop")
    (fb / "sites").mkdir(parents=True)
    (fb / "sites" / "apps.txt").write_text("frappe\n")
    info = _info(BenchRuntime.mount, tmp_path)
    apps = info.get_bench_apps()
    assert len(apps) == 1
    assert apps[0]["name"] == "frappe"
    assert apps[0]["ref"] == "develop"
    assert apps[0]["commit"]
