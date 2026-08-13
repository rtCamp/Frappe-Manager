"""
Characterization tests for the migration gate inside ``app_callback``.

``frappe_manager/commands/__init__.py`` runs a migration gate before every non-whitelisted
command. The gate exists in four near-identical ~34 line blocks (infra prompt, the bench
prompt nested inside the infra "update" path, and the standalone bench prompt), and none of
it was covered by the suite. A dedup refactor of those blocks is therefore invisible today.

These tests pin the CURRENT observable behaviour of the gate so the refactor is safe:

- which prompt is shown (exact question / choices / default / ``required_flag``)
- which ``MigrationExecutor`` is built (positional config, ``target_benches`` /
  ``migrate_fm_infrastructure``, ``auto_proceed``, ``on_failure``, ``output_handler``)
- that ``execute()`` runs *inside* ``temporary_stop(output)``
- that a falsy ``execute()`` result means ``display_error`` + exit code 1 and that the new
  version is NOT recorded
- that success records the version (``set_system_migration_version`` / ``set_bench_migration_version``)
- that answering "skip" refuses the command with exit code 1
- the whitelist seams (invoked command, full command path, bench-command skip list)
- the non-interactive route: ``required_flag`` makes the prompt raise ``NonInteractiveError``
  instead of silently defaulting to "update"

Nothing here touches docker, the network, real ``~/frappe`` or real stdin: the gate's
collaborators are mocked at their seams and all paths live under ``tmp_path``.
"""

import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager.commands import app_callback
from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler

CURRENT_FM_VERSION = "0.99.0"
OLD_VERSION = "0.98.0"


class MigrationGateHarness:
    """Drives the real ``app_callback`` with every gate collaborator mocked at its seam."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch

        self.cli_dir = tmp_path / "fm"
        self.benches_dir = self.cli_dir / "sites"
        self.benches_dir.mkdir(parents=True)
        self.fm_config_path = self.cli_dir / "fm_config.toml"
        self.fm_config_path.touch()

        self.current_version = Version(CURRENT_FM_VERSION)

        self.output = MagicMock(spec=OutputHandler)
        self.output.prompt_ask.side_effect = self._answer_prompt

        self.config = MagicMock(name="fm_config_manager")
        self.config.get_system_migration_version.return_value = self.current_version
        self.config.logs.file_level = "DEBUG"

        self.answers: list[str] = []
        self.execute_results: list[bool] = []
        self.executors: list[MagicMock] = []
        self.events: list[str] = []
        self.temporary_stop_args: list[object] = []

        self.set_bench_migration_version = MagicMock(name="set_bench_migration_version")
        self.services_manager_cls = MagicMock(name="ServicesManager")

        self._bench_needs: dict[str, bool] = {}
        self._bench_versions: dict[str, Version | None] = {}

        self.ctx: MagicMock | None = None

    # -- knobs -------------------------------------------------------------

    def set_infra_version(self, version: str) -> None:
        self.config.get_system_migration_version.return_value = Version(version)

    def add_bench(
        self,
        name: str,
        *,
        exists: bool = True,
        needs_migration: bool = False,
        version: str | None = OLD_VERSION,
    ) -> Path:
        path = self.benches_dir / name
        if exists:
            path.mkdir()
        self._bench_needs[name] = needs_migration
        self._bench_versions[name] = Version(version) if version else None
        return path

    # -- seam implementations ---------------------------------------------

    def _answer_prompt(self, **kwargs) -> str:
        self.events.append("prompt_ask")
        if not self.answers:
            raise AssertionError(f"gate asked an unexpected prompt: {kwargs.get('prompt')!r}")
        return self.answers.pop(0)

    def make_executor(self, *args, **kwargs) -> MagicMock:
        executor = MagicMock(name=f"MigrationExecutor#{len(self.executors)}")
        executor.init_args = args
        executor.init_kwargs = kwargs
        result = self.execute_results.pop(0) if self.execute_results else True

        def _execute():
            self.events.append("execute")
            return result

        executor.execute.side_effect = _execute
        self.executors.append(executor)
        return executor

    @contextmanager
    def fake_temporary_stop(self, handler):
        self.temporary_stop_args.append(handler)
        self.events.append("temporary_stop:enter")
        try:
            yield
        finally:
            self.events.append("temporary_stop:exit")

    def probe_bench_needs_migration(self, bench_path: Path, _version: Version) -> bool:
        self.events.append(f"bench_needs_migration:{bench_path.name}")
        return self._bench_needs.get(bench_path.name, False)

    def probe_bench_version(self, bench_path: Path) -> Version | None:
        return self._bench_versions.get(bench_path.name)

    # -- observation helpers ----------------------------------------------

    @property
    def prompts(self) -> list[dict]:
        return [call.kwargs for call in self.output.prompt_ask.call_args_list]

    @property
    def warnings(self) -> list[str]:
        return [call.args[0] for call in self.output.warning.call_args_list if call.args]

    @property
    def errors(self) -> list[str]:
        return [call.args[0] for call in self.output.display_error.call_args_list if call.args]

    # -- run ---------------------------------------------------------------

    def run(
        self,
        invoked_subcommand: str,
        *,
        bench_arg: str | None = None,
        argv: list[str] | None = None,
        non_interactive: bool = False,
    ) -> MagicMock:
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = invoked_subcommand
        ctx.params = {"benchname": bench_arg} if bench_arg else {}
        self.ctx = ctx

        if argv is None:
            argv = ["fm", invoked_subcommand] + ([bench_arg] if bench_arg else [])
        self.monkeypatch.setattr(sys, "argv", argv)

        app_callback(ctx, verbose=False, log_level=None, non_interactive=non_interactive, version=None)
        return ctx


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """A ``MigrationGateHarness`` with all gate seams patched for the duration of the test."""
    harness = MigrationGateHarness(tmp_path, monkeypatch)

    docker_client = MagicMock(name="DockerClient")
    docker_client.return_value.server_running.return_value = True

    fm_config_cls = MagicMock(name="FMConfigManager")
    fm_config_cls.import_from_toml.return_value = harness.config

    logging_handler_cls = MagicMock(name="LoggingOutputHandler")
    logging_handler_cls.return_value = harness.output

    with ExitStack() as stack:
        p = stack.enter_context
        p(patch("frappe_manager.commands.CLI_DIR", harness.cli_dir))
        p(patch("frappe_manager.commands.CLI_BENCHES_DIRECTORY", harness.benches_dir))
        p(patch("frappe_manager.commands.CLI_FM_CONFIG_PATH", harness.fm_config_path))
        p(patch("frappe_manager.commands.is_cli_help_called", return_value=False))
        p(patch("frappe_manager.commands.spinner", _fake_spinner))
        p(patch("frappe_manager.commands.DockerClient", docker_client))
        p(patch("frappe_manager.commands.FMConfigManager", fm_config_cls))
        p(patch("frappe_manager.commands.LoggingOutputHandler", logging_handler_cls))
        p(patch("frappe_manager.commands.get_current_fm_version", return_value=CURRENT_FM_VERSION))
        p(patch("frappe_manager.commands.bench_needs_migration", harness.probe_bench_needs_migration))
        p(patch("frappe_manager.commands.get_bench_migration_version", harness.probe_bench_version))
        p(patch("frappe_manager.commands.set_bench_migration_version", harness.set_bench_migration_version))
        p(patch("frappe_manager.commands.MigrationExecutor", side_effect=harness.make_executor))
        p(patch("frappe_manager.commands.temporary_stop", harness.fake_temporary_stop))
        p(patch("frappe_manager.commands.ServicesManager", harness.services_manager_cls))
        p(patch("frappe_manager.logger.log.get_logger", MagicMock(name="get_logger")))
        p(patch("frappe_manager.output_manager.theme.apply_output_theme", MagicMock()))
        p(patch("frappe_manager.output_manager.style.set_output_style", MagicMock()))
        yield harness


@contextmanager
def _nullcontext():
    yield


def _fake_spinner(*_args, **_kwargs):
    return _nullcontext()


def _infra_prompt_kwargs() -> dict:
    return {
        "prompt": "How would you like to proceed?",
        "choices": [
            {"name": "Update now (recommended)", "value": "update"},
            {"name": "Update later (run 'fm migrate' when ready)", "value": "skip"},
        ],
        "default": "update",
        "required_flag": "'fm migrate' (run migration explicitly)",
    }


def _bench_prompt_kwargs(bench: str) -> dict:
    return {
        "prompt": f"Migrate bench '{bench}' now?",
        "choices": [
            {"name": "Update now", "value": "update"},
            {"name": f"Update later (run 'fm migrate {bench}' when ready)", "value": "skip"},
        ],
        "default": "update",
        "required_flag": f"'fm migrate {bench}' (run migration explicitly)",
    }


class TestMigrationCheckWhitelist:
    """Which invocations are allowed to bypass the gate entirely."""

    def test_whitelisted_invoked_command_skips_the_gate(self, gate):
        gate.set_infra_version(OLD_VERSION)

        ctx = gate.run("list")

        assert gate.prompts == []
        assert gate.executors == []
        # the callback still completes and wires the context
        assert ctx.obj["fm_config_manager"] is gate.config
        assert ctx.obj["services"] is gate.services_manager_cls.return_value

    def test_whitelisted_full_command_path_skips_the_gate(self, gate):
        """``fm self compose`` is whitelisted by full command path, not by invoked command."""
        gate.set_infra_version(OLD_VERSION)

        gate.run("self", argv=["fm", "self", "compose"])

        assert gate.prompts == []
        assert gate.executors == []

    def test_flag_after_multi_level_command_still_resolves_the_whitelisted_path(self, gate):
        """argv parsing stops at flags: ``fm self compose --extra`` is still ``self compose``."""
        gate.set_infra_version(OLD_VERSION)

        gate.run("self", argv=["fm", "self", "compose", "--extra"])

        assert gate.prompts == []

    def test_path_like_argument_truncates_the_command_path_and_gates(self, gate):
        """argv parsing stops at path-like args, so ``self`` alone is not whitelisted."""
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]

        gate.run("self", argv=["fm", "self", "~/some/path"])

        assert [p["prompt"] for p in gate.prompts] == ["How would you like to proceed?"]

    def test_command_path_is_capped_at_two_levels_and_gates_when_not_whitelisted(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]

        gate.run("self", argv=["fm", "self", "update", "images"])

        assert [p["prompt"] for p in gate.prompts] == ["How would you like to proceed?"]

    def test_bare_argv_falls_back_to_the_invoked_command(self, gate):
        """With no argv arguments the whitelist decision uses ``ctx.invoked_subcommand``."""
        gate.set_infra_version(OLD_VERSION)

        gate.run("list", argv=["fm"])

        assert gate.prompts == []

    def test_flag_first_argv_falls_back_to_the_invoked_command(self, gate):
        gate.set_infra_version(OLD_VERSION)

        gate.run("list", argv=["fm", "--verbose", "list"])

        assert gate.prompts == []

    def test_non_whitelisted_command_with_stale_infra_is_gated(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]

        gate.run("start")

        assert [p["prompt"] for p in gate.prompts] == ["How would you like to proceed?"]

    @pytest.mark.parametrize("command", ["stop", "delete", "maintenance"])
    def test_bench_migration_never_probed_for_bench_skip_commands(self, gate, command):
        """``stop``/``delete``/``maintenance`` never consult bench migration state."""
        gate.add_bench("mysite.localhost", needs_migration=True)

        gate.run(command, bench_arg="mysite.localhost")

        assert not any(e.startswith("bench_needs_migration") for e in gate.events)
        assert gate.prompts == []

    def test_missing_bench_directory_is_not_probed(self, gate):
        gate.add_bench("ghost.localhost", exists=False, needs_migration=True)

        gate.run("start", bench_arg="ghost.localhost")

        assert not any(e.startswith("bench_needs_migration") for e in gate.events)
        assert gate.prompts == []

    def test_up_to_date_infra_without_bench_arg_asks_nothing(self, gate):
        gate.run("start")

        assert gate.prompts == []
        assert gate.executors == []


class TestInfraMigrationPrompt:
    """Scenario 1: fm infrastructure is behind the installed fm version."""

    def test_warning_and_prompt_shape(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]

        gate.run("start")

        assert gate.warnings == [f"FM infrastructure needs update: v{OLD_VERSION} -> v{CURRENT_FM_VERSION}"]
        assert gate.prompts == [_infra_prompt_kwargs()]

    def test_update_builds_infra_executor_and_runs_it_inside_temporary_stop(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]

        gate.run("start")

        assert len(gate.executors) == 1
        executor = gate.executors[0]
        assert executor.init_args == (gate.config,)
        assert executor.init_kwargs == {
            "migrate_fm_infrastructure": True,
            "auto_proceed": True,
            "on_failure": "rollback",
            "output_handler": gate.output,
        }
        executor.execute.assert_called_once_with()
        assert gate.events == ["prompt_ask", "temporary_stop:enter", "execute", "temporary_stop:exit"]
        assert gate.temporary_stop_args == [gate.output]

    def test_update_success_records_new_infra_version(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]

        gate.run("start")

        gate.config.set_system_migration_version.assert_called_once_with(gate.current_version)
        gate.config.export_to_toml.assert_called_once_with()
        assert gate.errors == []

    def test_failed_execute_errors_exits_and_does_not_record_version(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]
        gate.execute_results = [False]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start")

        assert exc.value.exit_code == 1
        assert gate.errors == ["FM infrastructure update failed"]
        gate.config.set_system_migration_version.assert_not_called()
        gate.config.export_to_toml.assert_not_called()
        gate.services_manager_cls.assert_not_called()

    def test_skip_refuses_the_command(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["skip"]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start")

        assert exc.value.exit_code == 1
        assert gate.errors == ["Cannot proceed - FM infrastructure migration required"]
        assert gate.executors == []
        gate.config.set_system_migration_version.assert_not_called()
        gate.services_manager_cls.assert_not_called()

    def test_any_non_update_answer_is_treated_as_skip(self, gate):
        """The branch is ``if choice == "update" ... else`` -- anything else refuses."""
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["something-else"]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start")

        assert exc.value.exit_code == 1
        assert gate.errors == ["Cannot proceed - FM infrastructure migration required"]


class TestBenchMigrationPromptWithCurrentInfra:
    """Scenario 2: infra is current, only the named bench is behind."""

    def test_warning_and_prompt_shape(self, gate):
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update"]

        gate.run("start", bench_arg="mysite.localhost")

        assert gate.warnings == [
            f"Bench 'mysite.localhost' needs migration: v{OLD_VERSION} -> v{CURRENT_FM_VERSION}",
        ]
        assert gate.prompts == [_bench_prompt_kwargs("mysite.localhost")]

    def test_update_builds_bench_executor_and_runs_it_inside_temporary_stop(self, gate):
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update"]

        gate.run("start", bench_arg="mysite.localhost")

        assert len(gate.executors) == 1
        executor = gate.executors[0]
        assert executor.init_args == (gate.config,)
        assert executor.init_kwargs == {
            "target_benches": ["mysite.localhost"],
            "auto_proceed": True,
            "on_failure": "rollback",
            "output_handler": gate.output,
        }
        executor.execute.assert_called_once_with()
        assert gate.events[-4:] == ["prompt_ask", "temporary_stop:enter", "execute", "temporary_stop:exit"]
        assert gate.temporary_stop_args == [gate.output]

    def test_update_success_records_bench_version_at_bench_path(self, gate):
        bench_path = gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update"]

        gate.run("start", bench_arg="mysite.localhost")

        gate.set_bench_migration_version.assert_called_once_with(bench_path, gate.current_version)
        assert gate.errors == []

    def test_failed_execute_errors_exits_and_does_not_record_version(self, gate):
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update"]
        gate.execute_results = [False]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start", bench_arg="mysite.localhost")

        assert exc.value.exit_code == 1
        assert gate.errors == ["Bench migration failed for 'mysite.localhost'"]
        gate.set_bench_migration_version.assert_not_called()
        gate.services_manager_cls.assert_not_called()

    def test_skip_refuses_the_command_and_names_it(self, gate):
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["skip"]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start", bench_arg="mysite.localhost")

        assert exc.value.exit_code == 1
        assert gate.warnings[-1] == "Skipped bench migration. Run 'fm migrate mysite.localhost' when ready."
        assert gate.errors == ["Cannot start 'mysite.localhost' - migration required"]
        assert gate.executors == []
        gate.set_bench_migration_version.assert_not_called()
        gate.services_manager_cls.assert_not_called()

    def test_refusal_message_uses_the_invoked_command_name(self, gate):
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["skip"]

        with pytest.raises(typer.Exit):
            gate.run("shell", bench_arg="mysite.localhost")

        assert gate.errors == ["Cannot shell 'mysite.localhost' - migration required"]

    def test_bench_arg_is_read_from_any_of_the_parameter_aliases(self, gate):
        """``get_bench_arg_from_context`` accepts benchname / sitename / bench_name."""
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update"]

        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {}
        ctx.invoked_subcommand = "start"
        ctx.params = {"sitename": "mysite.localhost"}
        gate.monkeypatch.setattr(sys, "argv", ["fm", "start", "mysite.localhost"])

        app_callback(ctx, verbose=False, log_level=None, non_interactive=False, version=None)

        assert gate.prompts == [_bench_prompt_kwargs("mysite.localhost")]

    def test_bench_without_recorded_version_is_not_prompted(self, gate):
        """
        ``bench_version`` falsy short-circuits the branch even when migration is needed.

        Pinned as-is: the bench silently proceeds unmigrated. See report -- suspicious.
        """
        gate.add_bench("mysite.localhost", needs_migration=True, version=None)

        gate.run("start", bench_arg="mysite.localhost")

        assert gate.prompts == []
        assert gate.executors == []
        gate.services_manager_cls.assert_called_once()

    def test_up_to_date_bench_is_not_prompted(self, gate):
        gate.add_bench("mysite.localhost", needs_migration=False)

        gate.run("start", bench_arg="mysite.localhost")

        assert gate.prompts == []
        assert gate.executors == []


class TestInfraThenBenchMigration:
    """Scenario 1 continued: after a successful infra update the bench gate runs nested."""

    def test_both_prompts_shown_in_order(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update", "update"]

        gate.run("start", bench_arg="mysite.localhost")

        assert gate.prompts == [_infra_prompt_kwargs(), _bench_prompt_kwargs("mysite.localhost")]

    def test_two_executors_built_infra_first_then_bench(self, gate):
        bench_path = gate.add_bench("mysite.localhost", needs_migration=True)
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update", "update"]

        gate.run("start", bench_arg="mysite.localhost")

        assert len(gate.executors) == 2
        assert gate.executors[0].init_kwargs["migrate_fm_infrastructure"] is True
        assert gate.executors[1].init_kwargs == {
            "target_benches": ["mysite.localhost"],
            "auto_proceed": True,
            "on_failure": "rollback",
            "output_handler": gate.output,
        }
        assert gate.events == [
            "bench_needs_migration:mysite.localhost",
            "prompt_ask",
            "temporary_stop:enter",
            "execute",
            "temporary_stop:exit",
            "prompt_ask",
            "temporary_stop:enter",
            "execute",
            "temporary_stop:exit",
        ]
        gate.config.set_system_migration_version.assert_called_once_with(gate.current_version)
        gate.set_bench_migration_version.assert_called_once_with(bench_path, gate.current_version)

    def test_failed_infra_update_never_reaches_the_bench_prompt(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update"]
        gate.execute_results = [False]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start", bench_arg="mysite.localhost")

        assert exc.value.exit_code == 1
        assert len(gate.prompts) == 1
        assert len(gate.executors) == 1
        gate.set_bench_migration_version.assert_not_called()

    def test_skipping_infra_never_reaches_the_bench_prompt(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["skip"]

        with pytest.raises(typer.Exit):
            gate.run("start", bench_arg="mysite.localhost")

        assert len(gate.prompts) == 1
        assert gate.errors == ["Cannot proceed - FM infrastructure migration required"]

    def test_failed_nested_bench_migration_exits_after_recording_infra_version(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update", "update"]
        gate.execute_results = [True, False]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start", bench_arg="mysite.localhost")

        assert exc.value.exit_code == 1
        assert gate.errors == ["Bench migration failed for 'mysite.localhost'"]
        gate.config.set_system_migration_version.assert_called_once_with(gate.current_version)
        gate.set_bench_migration_version.assert_not_called()
        gate.services_manager_cls.assert_not_called()

    def test_skipping_nested_bench_migration_refuses_the_command(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update", "skip"]

        with pytest.raises(typer.Exit) as exc:
            gate.run("start", bench_arg="mysite.localhost")

        assert exc.value.exit_code == 1
        assert gate.warnings[-1] == "Skipped bench migration. Run 'fm migrate mysite.localhost' when ready."
        assert gate.errors == ["Cannot start 'mysite.localhost' - migration required"]
        assert len(gate.executors) == 1
        gate.set_bench_migration_version.assert_not_called()

    def test_infra_update_without_bench_arg_asks_once_and_proceeds(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update"]

        ctx = gate.run("start")

        assert len(gate.prompts) == 1
        assert len(gate.executors) == 1
        gate.set_bench_migration_version.assert_not_called()
        assert ctx.obj["fm_config_manager"] is gate.config


class TestDuplicatedBenchBlocksAgree:
    """
    The nested and standalone bench blocks are duplicates today.

    A dedup refactor must keep them observationally identical; these tests pin that.
    """

    @staticmethod
    def _standalone(gate) -> tuple[dict, dict]:
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["update"]
        gate.run("start", bench_arg="mysite.localhost")
        return gate.prompts[-1], gate.executors[-1].init_kwargs

    def test_nested_and_standalone_bench_prompts_are_identical(self, gate, tmp_path, monkeypatch):
        standalone_prompt, standalone_kwargs = self._standalone(gate)

        # second run through the nested (post infra update) block, same harness state reset
        gate.output.prompt_ask.reset_mock()
        gate.executors.clear()
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update", "update"]
        gate.run("start", bench_arg="mysite.localhost")

        assert gate.prompts[-1] == standalone_prompt
        assert gate.executors[-1].init_kwargs == standalone_kwargs

    def test_nested_and_standalone_skip_messages_are_identical(self, gate):
        gate.add_bench("mysite.localhost", needs_migration=True)
        gate.answers = ["skip"]
        with pytest.raises(typer.Exit):
            gate.run("start", bench_arg="mysite.localhost")
        standalone = (gate.warnings[-1], gate.errors[-1])

        gate.output.warning.reset_mock()
        gate.output.display_error.reset_mock()
        gate.set_infra_version(OLD_VERSION)
        gate.answers = ["update", "skip"]
        with pytest.raises(typer.Exit):
            gate.run("start", bench_arg="mysite.localhost")

        assert (gate.warnings[-1], gate.errors[-1]) == standalone


class TestNonInteractiveRoute:
    """``--non-interactive`` must never silently pick the "update" default."""

    def test_interactive_mode_is_configured_from_the_flag_before_the_gate(self, gate):
        gate.answers = []

        gate.run("start", non_interactive=True)

        gate.output.set_interactive_mode.assert_called_once_with(non_interactive_flag=True)

    def test_prompt_error_propagates_out_of_the_callback(self, gate):
        gate.set_infra_version(OLD_VERSION)
        gate.output.prompt_ask.side_effect = NonInteractiveError(
            "Cannot prompt in non-interactive mode: How would you like to proceed?",
            suggestions=["Use: 'fm migrate' (run migration explicitly)"],
        )

        with pytest.raises(NonInteractiveError):
            gate.run("start", non_interactive=True)

        assert gate.executors == []
        gate.config.set_system_migration_version.assert_not_called()
        gate.services_manager_cls.assert_not_called()

    def test_required_flag_makes_a_real_handler_refuse_instead_of_defaulting(self):
        """
        The gate passes ``required_flag``, and a real handler in non-interactive mode raises
        rather than returning the "update" default. Pins the message the user actually sees.
        """
        handler = RichOutputHandler()
        handler.set_interactive_mode(non_interactive_flag=True)

        with pytest.raises(NonInteractiveError) as exc:
            handler.prompt_ask(**_infra_prompt_kwargs())

        assert "Cannot prompt in non-interactive mode: How would you like to proceed?" in str(exc.value)
        assert "Use: 'fm migrate' (run migration explicitly)" in str(exc.value)

        with pytest.raises(NonInteractiveError) as bench_exc:
            handler.prompt_ask(**_bench_prompt_kwargs("mysite.localhost"))

        assert "Cannot prompt in non-interactive mode: Migrate bench 'mysite.localhost' now?" in str(bench_exc.value)
        assert "Use: 'fm migrate mysite.localhost' (run migration explicitly)" in str(bench_exc.value)
