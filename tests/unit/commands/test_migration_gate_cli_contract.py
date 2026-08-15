"""
Regression tests for the bench-migration gate as the *real* CLI reaches it.

``tests/unit/commands/test_migration_gate_contract.py`` drives ``app_callback`` directly with a
hand-built ``MagicMock`` context whose ``ctx.params`` carries ``benchname``. Click never produces
that shape: ``app_callback`` is a *group* callback, so Click fills the group context with the
group's own options only (verbose/log-level/non-interactive/version) and clears ``ctx.args``
before invoking it. Driving the real ``app`` object through Click therefore exercises a code
path the MagicMock harness cannot reach, and that is what these tests do:

- the bench gate actually fires for ``fm start <bench>`` (it used to be unreachable, because
  ``get_bench_arg_from_context`` always returned None on the group context)
- refusing a command reports failure (exit 1), so ``fm start x && ...`` cannot read a skipped
  start as success
- ``stop`` keeps working on an unmigrated bench, as ``commands_skip_bench_migration`` states

Nothing here touches docker, the network, real ``~/frappe`` or real stdin: every collaborator is
mocked at its seam and all paths live under ``tmp_path``. Bench migration state is *not* mocked --
it is read from a real ``bench_config.toml`` under ``tmp_path``.
"""

import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands import app, check_bench_migration_required, get_bench_arg_from_argv
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.base import OutputHandler

CURRENT_FM_VERSION = "0.99.0"
OLD_VERSION = "0.98.0"
BENCH = "mysite.localhost"


@contextmanager
def _nullcontext(*_args, **_kwargs):
    yield


def _write_stale_bench(benches_dir: Path, name: str = BENCH) -> Path:
    """A bench on disk whose recorded migration version is behind the running fm."""
    path = benches_dir / name
    path.mkdir(parents=True)
    (path / "bench_config.toml").write_text(f'[migration_state]\nmigrated_to = "{OLD_VERSION}"\n')
    return path


class RealCliGate:
    """Drives the real ``app`` through Click with only the gate's collaborators mocked."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch

        self.cli_dir = tmp_path / "fm"
        self.benches_dir = self.cli_dir / "sites"
        self.benches_dir.mkdir(parents=True)
        self.fm_config_path = self.cli_dir / "fm_config.toml"
        self.fm_config_path.touch()

        self.answers: list[str] = []

        self.output = MagicMock(spec=OutputHandler)
        self.output.prompt_ask.side_effect = self._answer_prompt

        self.config = MagicMock(name="fm_config_manager")
        self.config.get_system_migration_version.return_value = Version(CURRENT_FM_VERSION)
        self.config.logs.file_level = "DEBUG"

        self.services_manager_cls = MagicMock(name="ServicesManager")
        self.bench_cls = MagicMock(name="Bench")

    def _answer_prompt(self, **kwargs) -> str:
        if not self.answers:
            raise AssertionError(f"gate asked an unexpected prompt: {kwargs.get('prompt')!r}")
        return self.answers.pop(0)

    def add_stale_bench(self, name: str = BENCH) -> Path:
        return _write_stale_bench(self.benches_dir, name)

    def invoke(self, argv: list[str]):
        # Production sys.argv mirrors the invocation; under pytest it is pytest's own argv.
        self.monkeypatch.setattr(sys, "argv", ["fm", *argv])
        return CliRunner().invoke(app, argv)

    @property
    def prompts(self) -> list[dict]:
        return [call.kwargs for call in self.output.prompt_ask.call_args_list]

    @property
    def errors(self) -> list[str]:
        return [call.args[0] for call in self.output.display_error.call_args_list if call.args]


@pytest.fixture
def cli_gate(tmp_path, monkeypatch):
    harness = RealCliGate(tmp_path, monkeypatch)

    docker_client = MagicMock(name="DockerClient")
    docker_client.return_value.server_running.return_value = True

    fm_config_cls = MagicMock(name="FMConfigManager")
    fm_config_cls.import_from_toml.return_value = harness.config

    logging_handler_cls = MagicMock(name="LoggingOutputHandler")
    logging_handler_cls.return_value = harness.output

    with ExitStack() as stack:
        p = stack.enter_context
        # The `benchname` argument validator resolves against the constant in its own module.
        p(patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", harness.benches_dir))
        p(patch("frappe_manager.commands.CLI_DIR", harness.cli_dir))
        p(patch("frappe_manager.commands.CLI_BENCHES_DIRECTORY", harness.benches_dir))
        p(patch("frappe_manager.commands.CLI_FM_CONFIG_PATH", harness.fm_config_path))
        p(patch("frappe_manager.commands.spinner", _nullcontext))
        p(patch("frappe_manager.commands.DockerClient", docker_client))
        p(patch("frappe_manager.commands.FMConfigManager", fm_config_cls))
        p(patch("frappe_manager.commands.LoggingOutputHandler", logging_handler_cls))
        p(patch("frappe_manager.commands.get_current_fm_version", return_value=CURRENT_FM_VERSION))
        p(patch("frappe_manager.commands.ServicesManager", harness.services_manager_cls))
        # Belt and braces: the gate must never build a real executor in a unit test.
        p(patch("frappe_manager.commands.MigrationExecutor", MagicMock(name="MigrationExecutor")))
        p(patch("frappe_manager.commands.set_bench_migration_version", MagicMock()))
        p(patch("frappe_manager.commands.stop.Bench", harness.bench_cls))
        p(patch("frappe_manager.commands.stop.spinner", _nullcontext))
        p(patch("frappe_manager.logger.log.get_logger", MagicMock(name="get_logger")))
        yield harness


class TestBenchGateThroughTheRealCli:
    """The callback's bench gate has to fire for a real ``fm <cmd> <bench>`` invocation."""

    def test_start_on_a_stale_bench_prompts_the_bench_gate(self, cli_gate):
        """
        Regression: ``bench_arg`` came from ``ctx.params``, which a group callback never
        populates with the subcommand's argument, so this prompt never appeared in production.
        """
        cli_gate.add_stale_bench()
        cli_gate.answers = ["skip"]

        cli_gate.invoke(["start", BENCH])

        assert [prompt["prompt"] for prompt in cli_gate.prompts] == [f"Migrate bench '{BENCH}' now?"]

    def test_refusing_the_bench_migration_fails_the_command(self, cli_gate):
        cli_gate.add_stale_bench()
        cli_gate.answers = ["skip"]

        result = cli_gate.invoke(["start", BENCH])

        assert result.exit_code == 1
        assert cli_gate.errors == [f"Cannot start '{BENCH}' - migration required"]
        cli_gate.services_manager_cls.assert_not_called()

    def test_global_flag_before_the_command_still_resolves_the_bench(self, cli_gate):
        """``fm -v start <bench>``: the bench token is not simply ``sys.argv[2]``."""
        cli_gate.add_stale_bench()
        cli_gate.answers = ["skip"]

        result = cli_gate.invoke(["-v", "start", BENCH])

        assert [prompt["prompt"] for prompt in cli_gate.prompts] == [f"Migrate bench '{BENCH}' now?"]
        assert result.exit_code == 1

    def test_up_to_date_bench_is_not_gated(self, cli_gate):
        bench = cli_gate.benches_dir / BENCH
        bench.mkdir()
        (bench / "bench_config.toml").write_text(f'[migration_state]\nmigrated_to = "{CURRENT_FM_VERSION}"\n')

        cli_gate.invoke(["start", BENCH])

        assert cli_gate.prompts == []


class TestStopIsNeverBlockedByMigration:
    """``commands_skip_bench_migration`` promises stop/delete always work; stop must honour it."""

    def test_stop_on_a_stale_bench_reaches_bench_stop(self, cli_gate):
        """
        Regression: ``stop()`` called ``check_bench_migration_required`` as its first statement,
        so an unmigrated bench printed "Run: fm migrate ..." and left the containers running.
        """
        cli_gate.add_stale_bench()

        result = cli_gate.invoke(["stop", BENCH])

        assert result.exit_code == 0
        assert cli_gate.prompts == []
        cli_gate.bench_cls.get_object.return_value.stop.assert_called_once_with()


class TestCheckBenchMigrationRequiredExitCode:
    """The non-interactive backstop every bench command calls."""

    @pytest.fixture
    def benches_dir(self, tmp_path, monkeypatch):
        benches = tmp_path / "sites"
        benches.mkdir()
        monkeypatch.setattr("frappe_manager.commands.CLI_BENCHES_DIRECTORY", benches)
        monkeypatch.setattr("frappe_manager.commands.get_current_fm_version", lambda: CURRENT_FM_VERSION)
        return benches

    def test_stale_bench_refuses_with_exit_code_1(self, benches_dir):
        """
        Regression: this raised ``typer.Exit(0)``, so ``fm start <stale bench> && ...`` treated a
        completely skipped start as success.
        """
        _write_stale_bench(benches_dir)

        with pytest.raises(typer.Exit) as exc:
            check_bench_migration_required(BENCH)

        assert exc.value.exit_code == 1

    def test_current_bench_is_not_refused(self, benches_dir):
        bench = benches_dir / BENCH
        bench.mkdir()
        (bench / "bench_config.toml").write_text(f'[migration_state]\nmigrated_to = "{CURRENT_FM_VERSION}"\n')

        assert check_bench_migration_required(BENCH) is None

    def test_unknown_bench_is_not_refused(self, benches_dir):
        assert check_bench_migration_required("does-not-exist.localhost") is None


class TestGetBenchArgFromArgv:
    """The argv scan that replaces the unreachable ``ctx.params`` lookup."""

    @pytest.mark.parametrize(
        ("argv", "command_path", "expected"),
        [
            (["fm", "start", BENCH], "start", BENCH),
            (["fm", "-v", "start", BENCH], "start", BENCH),
            (["fm", "start", BENCH, "--verbose"], "start", BENCH),
            (["fm", "start"], "start", None),
            (["fm", "ssl", "add", BENCH], "ssl add", BENCH),
            (["fm", "self", "compose"], "self compose", None),
            (["fm", "self", "~/some/path"], "self", None),
            (["fm"], "start", None),
        ],
    )
    def test_argv_scan(self, monkeypatch, argv, command_path, expected):
        monkeypatch.setattr(sys, "argv", argv)

        assert get_bench_arg_from_argv(command_path) == expected
