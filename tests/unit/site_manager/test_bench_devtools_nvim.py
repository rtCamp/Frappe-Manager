from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager import NVIM_DAP_LUA
from frappe_manager.site_manager.modules.bench_devtools import BenchDevTools


@pytest.fixture
def bench_devtools(tmp_path):
    return BenchDevTools(
        docker_client=MagicMock(),
        compose_file_manager=MagicMock(),
        bench_path=tmp_path,
        bench_name="mybench.localhost",
        is_running_fn=lambda: True,
        output_handler=MagicMock(),
    )


class TestNvimDebuggerSetup:
    def test_setup_neovim_debugger_warns_for_non_workspace_path(self, bench_devtools, mocker):
        sync_mock = mocker.patch.object(bench_devtools, "_sync_nvim_config_files")
        install_tools_mock = mocker.patch.object(bench_devtools, "_install_bench_dev_tools")

        bench_devtools.setup_neovim_debugger("/tmp/custom")

        bench_devtools.output.warning.assert_called_once_with(
            "Neovim debugger configuration is only supported for workspace directory",
        )
        sync_mock.assert_not_called()
        install_tools_mock.assert_not_called()

    def test_setup_neovim_debugger_syncs_and_installs_for_workspace_path(self, bench_devtools, mocker):
        sync_mock = mocker.patch.object(bench_devtools, "_sync_nvim_config_files")
        install_tools_mock = mocker.patch.object(bench_devtools, "_install_bench_dev_tools")

        bench_devtools.setup_neovim_debugger("/workspace/frappe-bench/")

        sync_mock.assert_called_once_with("workspace/frappe-bench")
        install_tools_mock.assert_called_once_with()
        bench_devtools.output.print.assert_called_once_with("Synced nvim-dap debugger configuration (.nvim.lua)")


class TestNvimConfigSync:
    def test_sync_nvim_config_files_writes_project_local_config(self, bench_devtools, tmp_path):
        bench_devtools._sync_nvim_config_files("workspace/frappe-bench")

        config_path = tmp_path / "workspace" / "frappe-bench" / ".nvim.lua"
        assert config_path.exists()
        assert config_path.read_text() == NVIM_DAP_LUA

    def test_sync_nvim_config_files_backs_up_existing_config(self, bench_devtools, tmp_path):
        workspace_dir = tmp_path / "workspace" / "frappe-bench"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        config_path = workspace_dir / ".nvim.lua"
        config_path.write_text("-- old config")

        bench_devtools._sync_nvim_config_files("workspace/frappe-bench")

        backup_files = list(workspace_dir.glob(".nvim.*.lua"))
        assert len(backup_files) == 1
        assert backup_files[0].read_text() == "-- old config"
        assert config_path.read_text() == NVIM_DAP_LUA
