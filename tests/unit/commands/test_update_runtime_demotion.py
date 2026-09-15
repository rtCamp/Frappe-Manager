"""
Characterization tests for ``fm update --runtime``: the image <-> mount runtime conversion.

``--runtime mount`` extracts an editable workspace from the bench's currently deployed image; it
is NOT a deploy (code on disk already equals what is running), so there is nothing to migrate and
no ``DeployOrchestrator`` run -- just a workspace materialize and a container recreate on the
mount compose shape. ``--runtime image`` never performs a conversion itself: on an already-image
bench it is a friendly no-op, and on a mount bench it is refused with a pointer to ``fm switch``,
which is the only path that can migrate a site onto a baked image.

What is pinned here is the decision table: already-mount/already-image are no-ops, a missing
recorded deployed image is refused, the extraction order (fetch -> stash -> materialize ->
recreate -> save), the mount -> image deploy-boundary refusal, and the new combination refusal
that keeps ``--runtime mount`` from running in the same invocation as a Python/Node/developer-mode
change on an image bench (see ``test_update_and_deploy_contract.py`` for the ordinary
``update()``/``switch()``/``prune()`` decision tables, which this file does not re-pin).
"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
import pytest
import typer
import typer.main

from frappe_manager import EnableDisableOptionsEnum
from frappe_manager.commands import app
from frappe_manager.commands.update import update
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.site_manager.bench_config import BenchRuntime

pytestmark = pytest.mark.timeout(15)

BENCH = "mybench.localhost"


@contextmanager
def _null_spinner(*_args, **_kwargs):
    yield


def _group() -> click.Group:
    group = typer.main.get_command(app)
    assert isinstance(group, click.Group)
    return group


def _long_flags(command_name: str) -> set[str]:
    """Every long flag (``--foo``) a command declares, primary or secondary (``--no-foo``)."""
    flags: set[str] = set()
    for param in _group().commands[command_name].params:
        if isinstance(param, click.Option):
            flags.update(param.opts)
            flags.update(param.secondary_opts)
    return flags


class DemotionWorld:
    """Drives the real ``update()`` with every collaborator replaced at its seam."""

    def __init__(self, tmp_path: Path, stack: ExitStack) -> None:
        self.output = MagicMock(spec=OutputHandler)
        # conftest installs a real RichOutputHandler globally; swap the INSTANCE (not the getter)
        # so the command's own get_global_output_handler() hands back an observable double.
        set_global_output_handler(self.output)

        self.services = MagicMock(name="services_manager")

        self.bench_path = tmp_path / "benches" / BENCH

        self.bench = MagicMock(name="Bench")
        self.bench.name = BENCH
        self.bench.site_name = BENCH
        self.bench.path = self.bench_path
        self.bench.running = True

        cfg = self.bench.bench_config
        cfg.runtime = BenchRuntime.image
        cfg.deploy_state = None
        cfg.export_to_compose_inputs.side_effect = dict
        cfg.environment_type = SimpleNamespace(value="dev")
        cfg.get_newrelic_config.return_value = None

        # A missing worker compose file means the demotion skips regenerating it -- set the
        # default explicitly, else a bare MagicMock() from an unconfigured `.exists()` is truthy
        # and every test would regenerate it.
        self.bench.workers.compose_file_manager.compose_path.exists.return_value = False

        self.bench_cls = MagicMock(name="Bench class")
        self.bench_cls.get_object.return_value = self.bench

        self.check_migration = MagicMock(name="check_bench_migration_required")

        self.fetch_image = MagicMock(name="fetch_image")
        self.stash_seed = MagicMock(name="stash_conflicting_seed_paths", return_value=None)
        self.materialize = MagicMock(name="materialize_workspace_from_image", return_value=[])

        p = stack.enter_context
        p(patch("frappe_manager.commands.update.Bench", self.bench_cls))
        p(patch("frappe_manager.commands.update.spinner", _null_spinner))
        p(patch("frappe_manager.commands.update.check_bench_migration_required", self.check_migration))
        p(patch("frappe_manager.site_manager.modules.transport.fetch_image", self.fetch_image))
        p(patch("frappe_manager.site_manager.modules.workspace_seed.stash_conflicting_seed_paths", self.stash_seed))
        p(
            patch(
                "frappe_manager.site_manager.modules.workspace_seed.materialize_workspace_from_image",
                self.materialize,
            )
        )

    # -- knobs -------------------------------------------------------------

    @property
    def config(self):
        return self.bench.bench_config

    # -- observation ---------------------------------------------------------

    @property
    def errors(self) -> list[str]:
        return [c.args[0] for c in self.output.display_error.call_args_list if c.args]

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list if c.args]

    @property
    def warnings(self) -> list[str]:
        return [c.args[0] for c in self.output.warning.call_args_list if c.args]

    @property
    def compose_up_calls(self) -> list:
        return self.bench.docker_client.compose.up.call_args_list

    @property
    def saves(self) -> int:
        return self.bench.save_bench_config.call_count

    # -- run -----------------------------------------------------------------

    def run(self, **kwargs):
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": self.services}
        return update(ctx, address=BENCH, **kwargs)


@pytest.fixture
def world(tmp_path):
    with ExitStack() as stack:
        yield DemotionWorld(tmp_path, stack)


class TestAlreadyMountIsANoOp:
    def test_runtime_mount_on_a_mount_bench_is_a_no_op(self, world):
        world.config.runtime = BenchRuntime.mount

        world.run(runtime=BenchRuntime.mount)

        assert world.prints == ["Bench runtime is already 'mount'"]
        world.fetch_image.assert_not_called()
        world.bench.generate_compose.assert_not_called()
        assert world.saves == 0


class TestAlreadyImageIsANoOp:
    def test_runtime_image_on_an_image_bench_is_a_no_op(self, world):
        world.run(runtime=BenchRuntime.image)

        assert world.prints == ["Bench runtime is already 'image'"]
        world.fetch_image.assert_not_called()
        assert world.saves == 0


class TestImageDirectionOnAMountBenchRefuses:
    def test_runtime_image_on_a_mount_bench_points_at_switch(self, world):
        world.config.runtime = BenchRuntime.mount

        with pytest.raises(typer.Exit) as exc:
            world.run(runtime=BenchRuntime.image)

        assert exc.value.exit_code == 1
        assert world.errors == [
            "mount -> image conversion runs through the deploy pipeline (it must migrate the site onto the "
            f"baked image) -- run 'fm switch {BENCH} REPO:TAG'."
        ]
        world.fetch_image.assert_not_called()
        assert world.saves == 0


class TestDemotionNeedsARecordedDeployedImage:
    @pytest.mark.parametrize(
        "state",
        [
            pytest.param(None, id="no-deploy-state"),
            pytest.param(SimpleNamespace(current_image=None), id="no-current-image"),
        ],
    )
    def test_demotion_refuses_without_a_recorded_image(self, world, state):
        world.config.deploy_state = state

        with pytest.raises(typer.Exit) as exc:
            world.run(runtime=BenchRuntime.mount)

        assert exc.value.exit_code == 1
        assert world.errors == ["No deployed image recorded; cannot materialize the workspace."]
        world.fetch_image.assert_not_called()
        assert world.config.runtime == BenchRuntime.image
        assert world.saves == 0


class TestDemotionHappyPath:
    def test_demotion_materializes_the_workspace_in_order_and_saves(self, world):
        world.config.deploy_state = SimpleNamespace(current_image="local/mybench:t7")
        world.materialize.return_value = ["apps", "env"]
        manager = MagicMock()
        manager.attach_mock(world.fetch_image, "fetch_image")
        manager.attach_mock(world.stash_seed, "stash_seed")
        manager.attach_mock(world.materialize, "materialize")

        world.run(runtime=BenchRuntime.mount)

        assert [c[0] for c in manager.mock_calls] == ["fetch_image", "stash_seed", "materialize"]
        frappe_bench_dir = world.bench_path / "workspace" / "frappe-bench"
        world.fetch_image.assert_called_once_with(world.bench.docker_client, "local/mybench:t7", output=world.output)
        world.stash_seed.assert_called_once_with(frappe_bench_dir, output=world.output)
        world.materialize.assert_called_once_with(
            world.bench.docker_client, "local/mybench:t7", frappe_bench_dir, output=world.output
        )
        assert world.config.runtime == BenchRuntime.mount
        assert "Extracted from image: apps, env" in world.prints
        assert world.saves == 1

    def test_demotion_reports_nothing_extracted_when_the_workspace_was_complete(self, world):
        world.config.deploy_state = SimpleNamespace(current_image="local/mybench:t7")
        world.materialize.return_value = []

        world.run(runtime=BenchRuntime.mount)

        assert "Extracted from image: nothing (already present)" in world.prints

    def test_demotion_warns_about_stashed_stale_code_but_continues(self, world):
        world.config.deploy_state = SimpleNamespace(current_image="local/mybench:t7")
        world.stash_seed.return_value = Path("/benches/x/workspace/frappe-bench.stash")

        world.run(runtime=BenchRuntime.mount)

        assert world.warnings == [
            "Existing workspace code was stale vs local/mybench:t7; moved to "
            "/benches/x/workspace/frappe-bench.stash -- review and delete it."
        ]
        world.materialize.assert_called_once()
        assert world.saves == 1

    def test_demotion_recreates_every_container_without_pulling(self, world):
        world.config.deploy_state = SimpleNamespace(current_image="local/mybench:t7")

        world.run(runtime=BenchRuntime.mount)

        assert world.compose_up_calls[0].kwargs == {"detach": True, "force_recreate": True, "pull": "never"}
        assert world.bench.workers.docker_client.compose.up.call_args.kwargs == {
            "services": [],
            "detach": True,
            "pull": "never",
            "stream": False,
        }

    def test_demotion_regenerates_worker_compose_only_when_it_exists(self, world):
        world.config.deploy_state = SimpleNamespace(current_image="local/mybench:t7")
        world.bench.workers.compose_file_manager.compose_path.exists.return_value = True

        world.run(runtime=BenchRuntime.mount)

        world.bench.workers.generate_compose.assert_called_once_with()

    def test_demotion_skips_worker_compose_when_it_does_not_exist(self, world):
        world.config.deploy_state = SimpleNamespace(current_image="local/mybench:t7")

        world.run(runtime=BenchRuntime.mount)

        world.bench.workers.generate_compose.assert_not_called()


class TestCombinationRefusal:
    """``--runtime mount`` cannot combine with a Python/Node/developer-mode change on an image
    bench: doing both in one invocation would need the demotion to finish before the immutability
    gate could ever pass, which is exactly the same-invocation composability that was removed.
    A refused update changes nothing, so this is a hoisted refusal, not a two-step pipeline."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"python_version": "3.12"}, id="python"),
            pytest.param({"node_version": "20"}, id="node"),
            pytest.param({"developer_mode": EnableDisableOptionsEnum.enable}, id="developer-mode-enable"),
        ],
    )
    def test_runtime_mount_with_a_mount_only_flag_on_an_image_bench_refuses(self, world, kwargs):
        with pytest.raises(typer.Exit) as exc:
            world.run(runtime=BenchRuntime.mount, **kwargs)

        assert exc.value.exit_code == 1
        assert world.errors == [
            "--runtime mount cannot combine with Python/Node/developer-mode changes in the same run: "
            f"demote first with 'fm update {BENCH} --runtime mount', then re-run with the workspace flags."
        ]
        world.fetch_image.assert_not_called()
        assert world.saves == 0


class TestSwitchLostTheRuntimeOption:
    def test_switch_help_no_longer_shows_runtime(self):
        assert "--runtime" not in _long_flags("switch")

    def test_updates_help_shows_runtime(self):
        assert "--runtime" in _long_flags("update")
