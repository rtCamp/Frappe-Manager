"""Bench info version source: image runtime reads baked image labels
(`fm.python.version` / `fm.node.version`); mount runtime reads the on-disk
uv/fnm symlinks. No container exec/run involved.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from frappe_manager.site_manager.bench_config import BenchRuntime
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.utils.site import read_bench_node_version, read_bench_python_version


def _make_bench(tmp_path):
    fb = tmp_path / "workspace" / "frappe-bench"
    (fb / ".uv").mkdir(parents=True)
    (fb / ".uv" / "python-default").symlink_to("cpython-3.12.9-linux-x86_64-gnu")
    (fb / ".fnm" / "aliases").mkdir(parents=True)
    (fb / ".fnm" / "aliases" / "default").symlink_to("../node-versions/v22.11.0/installation")
    return fb


def _info(runtime, tmp_path, docker_client=None, tag="repo:tag"):
    info = object.__new__(BenchInfo)  # bypass __init__ (no services/docker setup)
    info.bench_path = tmp_path
    info.docker_client = docker_client
    info.bench_config = SimpleNamespace(
        runtime=runtime,
        deploy_state=SimpleNamespace(current_tag=tag) if tag else None,
    )
    return info


def test_read_versions_from_symlinks(tmp_path):
    fb = _make_bench(tmp_path)
    assert read_bench_python_version(fb) == "3.12.9"
    assert read_bench_node_version(fb) == "v22.11.0"


def test_read_versions_missing_returns_none(tmp_path):
    assert read_bench_python_version(tmp_path) is None
    assert read_bench_node_version(tmp_path) is None


def test_image_runtime_reads_labels(tmp_path):
    dc = MagicMock()
    dc.image_labels.return_value = {"fm.python.version": "3.14.2", "fm.node.version": "v24.11.0"}
    info = _info(BenchRuntime.image, tmp_path, docker_client=dc)
    assert info.get_python_version() == "3.14.2"
    assert info.get_node_version() == "v24.11.0"
    dc.image_labels.assert_called_with("repo:tag")


def test_image_runtime_missing_label_is_na(tmp_path):
    dc = MagicMock()
    dc.image_labels.return_value = {}
    info = _info(BenchRuntime.image, tmp_path, docker_client=dc)
    assert info.get_python_version() == "N/A"
    assert info.get_node_version() == "N/A"


def test_image_runtime_no_tag_is_na(tmp_path):
    info = _info(BenchRuntime.image, tmp_path, docker_client=MagicMock(), tag=None)
    assert info.get_python_version() == "N/A"


def test_mount_runtime_reads_symlinks(tmp_path):
    _make_bench(tmp_path)
    info = _info(BenchRuntime.mount, tmp_path)
    assert info.get_python_version() == "3.12.9"
    assert info.get_node_version() == "v22.11.0"
