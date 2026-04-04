from unittest.mock import MagicMock

import typer

from frappe_manager.commands.nvim import nvim


class TestNvimCommand:
    def test_debugger_flag_triggers_neovim_config_generation(self, mocker):
        mock_services = MagicMock()
        mock_logger = MagicMock()
        mock_output = MagicMock()
        mock_bench = MagicMock()

        mocker.patch("frappe_manager.commands.nvim.check_bench_migration_required")
        mocker.patch("frappe_manager.commands.nvim.get_global_output_handler", return_value=mock_output)
        get_bench_mock = mocker.patch("frappe_manager.commands.nvim.Bench.get_object", return_value=mock_bench)

        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": mock_services, "logger": mock_logger}

        nvim(ctx, benchname="mybench.localhost", debugger=True, workdir="/workspace/frappe-bench")

        get_bench_mock.assert_called_once_with(
            "mybench.localhost",
            mock_services,
            logger=mock_logger,
            output_handler=mock_output,
        )
        mock_bench.setup_neovim_debugger.assert_called_once_with(workdir="/workspace/frappe-bench")

    def test_without_debugger_flag_displays_guidance_message(self, mocker):
        mock_output = MagicMock()
        mock_bench = MagicMock()

        mocker.patch("frappe_manager.commands.nvim.check_bench_migration_required")
        mocker.patch("frappe_manager.commands.nvim.get_global_output_handler", return_value=mock_output)
        mocker.patch("frappe_manager.commands.nvim.Bench.get_object", return_value=mock_bench)

        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": MagicMock(), "logger": MagicMock()}

        nvim(ctx, benchname="mybench.localhost", debugger=False)

        mock_bench.setup_neovim_debugger.assert_not_called()
        mock_output.print.assert_called_once()
        printed_message = mock_output.print.call_args[0][0]
        assert "No action specified" in printed_message
        assert "--debugger" in printed_message
