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


def test_nvim_lua_resolves_debugpy_from_bench_env():
    assert "resolve_debugpy_python" in NVIM_DAP_LUA
    assert "start_container_debug_session" in NVIM_DAP_LUA
    assert "build_env_args" in NVIM_DAP_LUA
    assert 'local with_env = { "env" }' in NVIM_DAP_LUA
    assert '"nohup " .. shell_join(launch_args)' in NVIM_DAP_LUA
    assert '"-m",' in NVIM_DAP_LUA
    assert '"debugpy",' in NVIM_DAP_LUA
    assert '"--wait-for-client",' in NVIM_DAP_LUA
    assert '"docker",' in NVIM_DAP_LUA
    assert '"/workspace/frappe-bench/env/bin/python"' in NVIM_DAP_LUA
    assert 'BENCH_ROOT .. "/env/bin/python"' in NVIM_DAP_LUA


def test_nvim_lua_uses_docker_compose_attach_flow():
    assert "infer_bench_path" in NVIM_DAP_LUA
    assert 'COMPOSE_FILE = BENCH_PATH ~= "" and (BENCH_PATH .. "/docker-compose.yml") or ""' in NVIM_DAP_LUA
    assert "dap.adapters.fm_python" in NVIM_DAP_LUA
    assert 'request = can_use_docker_compose() and "attach" or "launch"' in NVIM_DAP_LUA
    assert 'type = "server"' in NVIM_DAP_LUA
    assert '"ps", "-q", "frappe"' in NVIM_DAP_LUA
    assert '"inspect",' in NVIM_DAP_LUA
    assert '"{{json .NetworkSettings.Networks}}"' in NVIM_DAP_LUA
    assert "vim.json.decode" in NVIM_DAP_LUA
    assert '"compose",' in NVIM_DAP_LUA
    assert '"--listen",' in NVIM_DAP_LUA
    assert '"0.0.0.0:" .. tostring(port)' in NVIM_DAP_LUA
    assert "wait_for_debugpy_adapter" in NVIM_DAP_LUA
    assert '"debugpy/adapter" in command and f"--port {debug_port}" in command' in NVIM_DAP_LUA
    assert "attach bootstrap failed; falling back to local debugpy adapter" in NVIM_DAP_LUA
    assert 'fm_prelaunch = "fmx stop frappe >/dev/null 2>&1 || true; sleep 2"' in NVIM_DAP_LUA
    assert 'vim.fn.fnamemodify(dir, ":t") == "workspace"' in NVIM_DAP_LUA


def test_nvim_lua_uses_current_config_file_to_find_bench_root():
    assert "get_config_dir" in NVIM_DAP_LUA
    assert 'debug.getinfo(1, "S").source' in NVIM_DAP_LUA


def test_nvim_lua_has_path_mappings_for_host_to_container_sources():
    assert "pathMappings" in NVIM_DAP_LUA
    assert 'local HOST_WORKSPACE_ROOT = BENCH_PATH ~= "" and (BENCH_PATH .. "/workspace") or BENCH_ROOT' in NVIM_DAP_LUA
    assert "localRoot = HOST_WORKSPACE_ROOT" in NVIM_DAP_LUA
    assert 'remoteRoot = "/workspace"' in NVIM_DAP_LUA


def test_nvim_lua_does_not_probe_debugpy_port_before_attach():
    assert "wait_for_port" not in NVIM_DAP_LUA
    assert "is_port_open" not in NVIM_DAP_LUA
    assert 'connect_ex((sys.argv[1], int(sys.argv[2])))' not in NVIM_DAP_LUA
    assert "DEBUGPY_ATTACH_DELAY_MS" not in NVIM_DAP_LUA
    assert "wait_for_debugpy_adapter" in NVIM_DAP_LUA


def test_nvim_lua_registers_handlers_for_debugpy_extension_events():
    assert "event_debugpySockets" in NVIM_DAP_LUA
    assert "event_debugpyWaitingForServer" in NVIM_DAP_LUA
    assert 'dap.listeners.before.event_debugpySockets["fm_debugpy_events"]' in NVIM_DAP_LUA
    assert 'dap.listeners.before.event_debugpyWaitingForServer["fm_debugpy_events"]' in NVIM_DAP_LUA


def test_nvim_lua_cleans_up_stale_debugpy_processes_before_launch():
    assert "cleanup_matching_debugpy_processes" in NVIM_DAP_LUA
    assert 'fm_cleanup_matchers = { "frappe serve", "--port", "80" }' in NVIM_DAP_LUA
    assert "fm_cleanup_debug_port = 5678" in NVIM_DAP_LUA


def test_nvim_lua_cleanup_skips_its_own_helper_process():
    assert "current_pid = os.getpid()" in NVIM_DAP_LUA
    assert "if pid == current_pid:" in NVIM_DAP_LUA
    assert "continue" in NVIM_DAP_LUA


def test_nvim_lua_registers_fm_debug_commands_and_keymaps():
    assert "replace_user_command" in NVIM_DAP_LUA
    assert 'replace_user_command("FmDebugStart"' in NVIM_DAP_LUA
    assert 'replace_user_command("FmDebugQueue"' in NVIM_DAP_LUA
    assert 'replace_user_command("FmDebugFunction"' in NVIM_DAP_LUA
    assert 'replace_user_command("FmDebugToggleUI"' in NVIM_DAP_LUA
    assert 'vim.keymap.set("n", "<leader>ds"' in NVIM_DAP_LUA
    assert 'vim.keymap.set("n", "<leader>du"' in NVIM_DAP_LUA


def test_nvim_lua_integrates_with_dapui_and_virtual_text_when_available():
    assert 'pcall(require, "dapui")' in NVIM_DAP_LUA
    assert 'dap.listeners.after.event_initialized["fm_dapui"]' in NVIM_DAP_LUA
    assert 'dapui.open()' in NVIM_DAP_LUA
    assert 'dapui.toggle({})' in NVIM_DAP_LUA
    assert 'setup_dapui_listeners()' in NVIM_DAP_LUA
