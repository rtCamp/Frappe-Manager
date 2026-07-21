"""Characterization test for the shared `provision()` seam.

Locks the provisioning call sequence that `BenchOrchestrator._phase2_initialize_bench`
performed before extraction: clone -> (setup runtimes iff a version is set) -> install
deps + build, all threaded with the same `use_run` flag. Provisioning shells to Docker,
so we characterize at the call seam with a mocked BenchAppManager (no Docker needed).
"""

from unittest.mock import MagicMock

from frappe_manager.site_manager.provisioner import provision


def _app_manager(python_version=None, node_version=None):
    am = MagicMock()
    am.bench_config.python_version = python_version
    am.bench_config.node_version = node_version
    return am


def _seq(am):
    return [c[0] for c in am.mock_calls if c[0] in ("install_apps", "setup_python_and_node_environments")]


def test_clone_then_setup_then_install_when_version_set():
    am = _app_manager(python_version="3.11")

    provision(am, [MagicMock()], output=MagicMock(), use_run=True, detect_versions=False)

    assert _seq(am) == ["install_apps", "setup_python_and_node_environments", "install_apps"]

    first = am.install_apps.call_args_list[0].kwargs
    assert first["clone_only"] is True
    assert first["use_run"] is True
    assert "skip_clone" not in first

    last = am.install_apps.call_args_list[1].kwargs
    assert last["skip_clone"] is True
    assert last["use_run"] is True
    assert "clone_only" not in last

    am.setup_python_and_node_environments.assert_called_once_with(use_run=True, recreate_python_env=True)


def test_env_setup_skipped_when_no_version():
    am = _app_manager(python_version=None, node_version=None)

    provision(am, [MagicMock()], output=MagicMock(), use_run=True, detect_versions=False)

    assert _seq(am) == ["install_apps", "install_apps"]
    am.setup_python_and_node_environments.assert_not_called()


def test_forwards_use_uv_token_and_run_flag():
    am = _app_manager()

    provision(
        am,
        [MagicMock()],
        output=MagicMock(),
        use_uv=False,
        github_token="tok",  # noqa: S106
        use_run=False,
        detect_versions=False,
    )

    for c in am.install_apps.call_args_list:
        assert c.kwargs["use_uv"] is False
        assert c.kwargs["github_token"] == "tok"  # noqa: S105
        assert c.kwargs["use_run"] is False
