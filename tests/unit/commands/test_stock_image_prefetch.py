"""Which commands pay the first-install image prefetch.

`app_callback` runs before every command, and on a machine with no `fm_config.toml` it
warms the whole stock stack first: frappe, nginx, two redis, mariadb, nginx-proxy,
mailpit, adminer. That exists so a first `fm create` does not stall halfway through a
pull, and for `fm create` it is the right thing.

`fm bake` runs none of those containers. It builds an image, pulling only the base image
it is told to build FROM. Prefetching the stack for it is waste, and on a CI runner it is
waste charged to every job, which is what these tests pin.

Everything is mocked at its seam: no docker, no network, no real `~/frappe`. The prefetch
itself is mocked, so what is asserted is whether fm decides to call it.
"""

import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager import STOCK_IMAGE_PREFETCH_SKIP_COMMANDS
from frappe_manager.commands import app
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager.base import OutputHandler

FM_VERSION = "0.99.0"


@contextmanager
def _nullcontext(*_args, **_kwargs):
    yield


class Harness:
    """The real `app` driven through Click, with a CLI_DIR that has no fm_config.toml."""

    def __init__(self, tmp_path, monkeypatch):
        self.monkeypatch = monkeypatch
        self.cli_dir = tmp_path / "fm"
        self.benches_dir = self.cli_dir / "sites"
        self.benches_dir.mkdir(parents=True)
        # Deliberately NOT created: its absence is what arms the prefetch.
        self.fm_config_path = self.cli_dir / "fm_config.toml"

        self.output = MagicMock(spec=OutputHandler)
        # The real `exit` raises typer.Exit; a mock that returns None lets the callback run on past
        # its own refusals, which is the difference between testing a guard and testing nothing.
        self.output.exit.side_effect = typer.Exit(1)
        self.config = MagicMock(name="fm_config_manager")
        self.config.get_system_migration_version.return_value = Version(FM_VERSION)
        self.config.logs.file_level = "DEBUG"
        self.pull = MagicMock(name="pull_docker_images", return_value=True)
        # Whether the stock images are already here is now asked of docker rather than inferred
        # from the config being new, so the seam has to be driven explicitly: a unit test must not
        # answer it from whatever images the developer's own daemon happens to hold.
        self.images_missing = MagicMock(name="stock_images_missing", return_value=True)

    def invoke(self, argv):
        self.monkeypatch.setattr(sys, "argv", ["fm", *argv])
        return CliRunner().invoke(app, argv)

    @property
    def prefetched(self) -> bool:
        return self.pull.called


@pytest.fixture
def cli(tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch)

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
        p(patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", harness.benches_dir))
        p(patch("frappe_manager.commands.spinner", _nullcontext))
        p(patch("frappe_manager.commands.DockerClient", docker_client))
        p(patch("frappe_manager.commands.FMConfigManager", fm_config_cls))
        p(patch("frappe_manager.commands.LoggingOutputHandler", logging_handler_cls))
        p(patch("frappe_manager.commands.get_current_fm_version", return_value=FM_VERSION))
        p(patch("frappe_manager.commands.pull_docker_images", harness.pull))
        p(patch("frappe_manager.commands.stock_images_missing", harness.images_missing))
        # Stop each command before it does real work; the callback has already run by then.
        p(patch("frappe_manager.commands.bake.BakeManager", MagicMock()))
        p(patch("frappe_manager.commands.ServicesManager", MagicMock()))
        yield harness


class TestPrefetchIsSkipped:
    def test_bake_does_not_prefetch_the_stock_stack(self, cli):
        """It builds an image and pulls the base image itself. The stack is unrelated."""
        cli.invoke(["bake", "--apps", "frappe", "--app-image", "localhost/x:t1"])

        assert not cli.prefetched

    def test_the_skip_survives_a_bench_argument(self, cli):
        """The decision is made on the command, not on how it was called."""
        cli.invoke(["bake", "mybench", "--app-image", "localhost/x:t1"])

        assert not cli.prefetched


class TestPrefetchStillHappens:
    def test_a_command_that_runs_the_stack_still_prefetches(self, cli):
        """`fm start` needs the stock images, so the first-install warmup keeps working."""
        cli.invoke(["start", "mybench"])

        assert cli.prefetched

    def test_an_observer_does_not_prefetch(self, cli):
        """`fm list` on a fresh host has nothing to list and must not spend minutes pulling.

        It used to: the exemption only covered `bake`, so the first read-only command on a new
        machine pulled all eight stock images, and a failed pull then deleted the whole fm home.
        """
        cli.invoke(["list"])

        assert not cli.prefetched

    def test_an_observer_running_first_does_not_disarm_the_prefetch(self, cli):
        """The bug a fresh-install E2E found: `fm list` then `fm create` died on missing images.

        The prefetch used to fire on `config_is_new`, a PROXY for "this host is bare". `fm list`
        is exempt from prefetching but still builds a ServicesManager, which writes
        fm_config.toml -- so the observer consumed the signal without doing the work, and every
        later command read an established host with not one image pulled.
        """
        cli.invoke(["list"])
        assert not cli.prefetched

        cli.fm_config_path.write_text('[logs]\nfile_level = "DEBUG"\n')
        cli.invoke(["start", "mybench"])

        assert cli.prefetched

    def test_a_warm_host_pulls_nothing(self, cli):
        """The trigger is the images being absent, so an established host pays no pull."""
        cli.images_missing.return_value = False

        cli.invoke(["start", "mybench"])

        assert not cli.prefetched

    def test_every_command_outside_the_exemption_prefetches(self, cli):
        """Guards against the exemption widening by accident to commands that need images."""
        assert "create" not in STOCK_IMAGE_PREFETCH_SKIP_COMMANDS
        assert "start" not in STOCK_IMAGE_PREFETCH_SKIP_COMMANDS


class TestTheConfigIsCreatedBeforeTheGatesReadIt:
    """A command that never saves a setting (`fm info` on a missing bench) would otherwise leave a
    fresh host with NO config at all, and the migration gates read an absent ledger as 0.0.0. The
    callback creates it; `export_to_toml` is what stamps a file it creates, which is tested against
    a real file in tests/unit/test_metadata_manager_baseline.py.
    """

    def test_a_prefetch_exempt_command_still_creates_the_config(self, cli):
        """`fm list` on a fresh host: no images pulled, config written anyway. It used to write the
        file only as a side effect of saving the auto-sized subnet, which carried no version."""
        cli.invoke(["list"])

        assert not cli.prefetched
        cli.config.export_to_toml.assert_called_once()

    def test_a_command_that_prefetches_creates_it_too(self, cli):
        cli.invoke(["start", "mybench"])

        assert cli.prefetched
        cli.config.export_to_toml.assert_called_once()

    def test_a_failed_prefetch_writes_nothing(self, cli):
        """Half an install is not a complete one: a config stamped current would tell every later
        command there is nothing left to set up."""
        cli.pull.return_value = False

        cli.invoke(["start", "mybench"])

        cli.config.export_to_toml.assert_not_called()

    def test_an_existing_config_is_not_rewritten(self, cli):
        """A host whose config predates this fm must still be told to migrate, not quietly
        declared current."""
        cli.fm_config_path.write_text('[logs]\nfile_level = "DEBUG"\n')

        cli.invoke(["list"])

        cli.config.export_to_toml.assert_not_called()


class TestAFailedFirstInstallLeavesNothingBehind:
    """A first command that cannot pull its images must not leave a half-made fm home: the next
    command would find `~/frappe` present, read as an existing install, and skip the very setup
    that never finished."""

    def _run(self, tmp_path, monkeypatch, *, home_exists: bool):
        cli_dir = tmp_path / "fm"
        if home_exists:
            (cli_dir / "sites").mkdir(parents=True)

        output = MagicMock(spec=OutputHandler)
        output.exit.side_effect = typer.Exit(1)
        config = MagicMock(name="fm_config_manager")
        config.get_system_migration_version.return_value = Version(FM_VERSION)
        config.logs.file_level = "DEBUG"

        docker_client = MagicMock(name="DockerClient")
        docker_client.return_value.server_running.return_value = True
        fm_config_cls = MagicMock(name="FMConfigManager")
        fm_config_cls.import_from_toml.return_value = config

        # The real LoggingOutputHandler opens CLI_DIR/logs/fm.log, which CREATES the home. That
        # side effect is the whole bug: whether the home pre-existed has to be read before it.
        def make_handler(_inner):
            (cli_dir / "logs").mkdir(parents=True, exist_ok=True)
            return output

        monkeypatch.setattr(sys, "argv", ["fm", "start", "mybench"])
        with ExitStack() as stack:
            p = stack.enter_context
            p(patch("frappe_manager.commands.CLI_DIR", cli_dir))
            p(patch("frappe_manager.commands.CLI_BENCHES_DIRECTORY", cli_dir / "sites"))
            p(patch("frappe_manager.commands.CLI_FM_CONFIG_PATH", cli_dir / "fm_config.toml"))
            p(patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", cli_dir / "sites"))
            p(patch("frappe_manager.commands.spinner", _nullcontext))
            p(patch("frappe_manager.commands.DockerClient", docker_client))
            p(patch("frappe_manager.commands.FMConfigManager", fm_config_cls))
            p(patch("frappe_manager.commands.LoggingOutputHandler", make_handler))
            p(patch("frappe_manager.commands.get_current_fm_version", return_value=FM_VERSION))
            p(patch("frappe_manager.commands.pull_docker_images", return_value=False))
            p(patch("frappe_manager.commands.stock_images_missing", return_value=True))
            CliRunner().invoke(app, ["start", "mybench"])
        return cli_dir

    def test_the_home_this_run_created_is_removed_again(self, tmp_path, monkeypatch):
        cli_dir = self._run(tmp_path, monkeypatch, home_exists=False)

        assert not cli_dir.exists()

    def test_an_existing_home_survives_a_failed_pull(self, tmp_path, monkeypatch):
        """The other half: wiping unconditionally deleted an existing install's logs and backups
        because one image pull failed."""
        cli_dir = self._run(tmp_path, monkeypatch, home_exists=True)

        assert cli_dir.exists()


class TestTheExemptionIsRealCommands:
    @pytest.mark.parametrize("name", sorted(STOCK_IMAGE_PREFETCH_SKIP_COMMANDS))
    def test_each_exempt_name_is_a_command_fm_actually_has(self, name):
        """A typo here would exempt nothing and nobody would notice."""
        import typer.main

        registered = typer.main.get_command(app).commands  # pyright: ignore[reportAttributeAccessIssue]

        assert name in registered
