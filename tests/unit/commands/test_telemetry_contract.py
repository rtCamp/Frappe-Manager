"""Contract for `fm telemetry enable` / `disable` / `status`.

These three own what `fm update --newrelic/--newrelic-license-key` used to. What matters here is
not the plumbing but the decisions:

* an enable with no key anywhere is refused BEFORE anything is written, because a bench recorded
  as enabled-without-a-key reports nothing at all (the exporter emits no env vars and the wrapper
  runs plain gunicorn) while every surface calls it enabled;
* a repeat of a reached goal state is reported, not re-applied: the retired flags had no equality
  check and force-recreated the web container on every invocation;
* the config is saved BEFORE the container work, so a failed recreate cannot leave
  bench_config.toml disagreeing with the running container;
* the agent's own `newrelic.ini` is the operator's file -- an off/on cycle must not offer to
  rewrite it, and only `--force-config` asks for the generated one back.

The container recreate is deliberately a recreate and not a restart: a container keeps the
environment it was created with, so a restart would re-run the old NEWRELIC_ENABLED and report
success having changed nothing.
"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from frappe_manager import TelemetryProviderEnum
from frappe_manager.commands.telemetry.disable import disable
from frappe_manager.commands.telemetry.enable import enable
from frappe_manager.commands.telemetry.status import status
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.site_manager.bench_config import TelemetryConfig, NewRelicConfig
from frappe_manager.site_manager.exceptions import BenchNotRunning

pytestmark = pytest.mark.timeout(15)

BENCH = "mybench.localhost"


@contextmanager
def _null_spinner(*_args, **_kwargs):
    yield


class TelemetryWorld:
    """Drives the real commands with every collaborator replaced at its seam."""

    def __init__(self, tmp_path: Path, stack: ExitStack) -> None:
        self.output = MagicMock(spec=OutputHandler)
        set_global_output_handler(self.output)

        self.bench_path = tmp_path / "benches" / BENCH
        self.bench_path.mkdir(parents=True)

        self.services = MagicMock(name="services_manager")
        self.bench = MagicMock(name="Bench")
        self.bench.name = BENCH
        self.bench.site_name = BENCH
        self.bench.path = self.bench_path
        self.bench.running = True

        cfg = self.bench.bench_config
        cfg.telemetry = None
        # The commands read monitoring through the helper and write it back through the
        # attribute, so the double has to keep the two consistent.
        cfg.get_telemetry_config.side_effect = lambda provider="newrelic": getattr(cfg.telemetry, provider, None) if cfg.telemetry else None
        cfg.export_to_compose_inputs.side_effect = dict

        bench_cls = MagicMock(name="Bench class")
        bench_cls.get_object.return_value = self.bench
        self.check_migration = MagicMock(name="check_bench_migration_required")

        p = stack.enter_context
        for module in ("enable", "disable", "status"):
            p(patch(f"frappe_manager.commands.telemetry.{module}.Bench", bench_cls))
            p(patch(f"frappe_manager.commands.telemetry.{module}.check_bench_migration_required", self.check_migration))
        p(patch("frappe_manager.commands.telemetry.enable.spinner", _null_spinner))
        p(patch("frappe_manager.commands.telemetry.disable.spinner", _null_spinner))
        p(patch("frappe_manager.commands.telemetry._helpers.Bench", bench_cls))

    # -- knobs -------------------------------------------------------------

    @property
    def config(self):
        return self.bench.bench_config

    def store(self, *, enabled: bool = False, license_key: str | None = None) -> None:
        self.config.telemetry = TelemetryConfig(newrelic=NewRelicConfig(enabled=enabled, license_key=license_key))

    def seed_agent_config(self) -> Path:
        path = self.bench_path / "workspace" / "frappe-bench" / "config" / "newrelic.ini"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[newrelic]\napp_name = hand tuned\n")
        return path

    # -- observation -------------------------------------------------------

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list if c.args]

    @property
    def compose_up_calls(self) -> list:
        return self.bench.docker_client.compose.up.call_args_list

    @property
    def saves(self) -> int:
        return self.bench.save_bench_config.call_count

    # -- run ---------------------------------------------------------------

    def _ctx(self):
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": self.services}
        return ctx

    def enable(self, **kwargs):
        return enable(self._ctx(), benchname=BENCH, provider=TelemetryProviderEnum.newrelic, **kwargs)

    def disable(self):
        return disable(self._ctx(), benchname=BENCH, provider=TelemetryProviderEnum.newrelic)

    def status(self):
        return status(self._ctx(), benchname=BENCH)


@pytest.fixture
def world(tmp_path):
    with ExitStack() as stack:
        yield TelemetryWorld(tmp_path, stack)


class TestEnable:
    def test_a_first_enable_with_no_key_anywhere_is_refused_before_any_write(self, world):
        with pytest.raises(typer.BadParameter) as exc:
            world.enable()

        assert exc.value.message == "--license-key is required the first time you enable NewRelic."
        assert world.saves == 0
        world.bench.generate_compose.assert_not_called()
        assert world.compose_up_calls == []

    def test_a_key_is_recorded_and_carried_to_the_container(self, world):
        world.enable(license_key="ingest-key")

        assert world.config.telemetry.newrelic.enabled is True
        assert world.config.telemetry.newrelic.license_key == "ingest-key"
        world.bench.supervisor.setup_newrelic.assert_called_once_with(world.bench_path, force=False)
        assert world.compose_up_calls[0].kwargs == {"services": ["frappe"], "detach": True, "force_recreate": True}

    def test_the_stored_key_is_reused_when_re_enabling_after_a_disable(self, world):
        world.store(enabled=False, license_key="stored-key")

        world.enable()

        assert world.config.telemetry.newrelic.enabled is True
        assert world.config.telemetry.newrelic.license_key == "stored-key"

    def test_config_is_persisted_before_the_container_is_touched(self, world):
        """A failed recreate must not leave the compose file and container ahead of the config."""
        world.bench.docker_client.compose.up.side_effect = RuntimeError("daemon gone")

        with pytest.raises(RuntimeError):
            world.enable(license_key="ingest-key")

        assert world.saves == 1

    def test_an_already_enabled_bench_is_reported_not_re_applied(self, world):
        """The retired `fm update --newrelic` had no equality check, so a repeat destroyed and
        recreated the web container every time."""
        world.store(enabled=True, license_key="stored-key")

        world.enable()

        assert any("already enabled" in line for line in world.prints)
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_a_rotation_still_applies_on_an_already_enabled_bench(self, world):
        """The no-op guard must not swallow a new key."""
        world.store(enabled=True, license_key="old-key")

        world.enable(license_key="new-key")

        assert world.config.telemetry.newrelic.license_key == "new-key"
        assert len(world.compose_up_calls) == 1

    def test_force_config_reaches_the_agent_config_writer(self, world):
        world.store(enabled=True, license_key="stored-key")

        world.enable(force_config=True)

        world.bench.supervisor.setup_newrelic.assert_called_once_with(world.bench_path, force=True)

    def test_a_stopped_bench_is_refused_before_any_write(self, world):
        world.bench.running = False

        with pytest.raises(BenchNotRunning):
            world.enable(license_key="ingest-key")

        assert world.saves == 0
        world.bench.generate_compose.assert_not_called()


class TestDisable:
    def test_it_clears_the_flag_and_recreates_the_web_container(self, world):
        world.store(enabled=True, license_key="stored-key")

        world.disable()

        assert world.config.telemetry.newrelic.enabled is False
        assert world.compose_up_calls[0].kwargs == {"services": ["frappe"], "detach": True, "force_recreate": True}

    def test_the_stored_key_survives_a_disable(self, world):
        """Re-enabling must need no arguments, so the key is kept in bench_config.toml. It leaves
        the COMPOSE file (generate_compose pops it); this is the recorded value."""
        world.store(enabled=True, license_key="stored-key")

        world.disable()

        assert world.config.telemetry.newrelic.license_key == "stored-key"

    def test_disabling_never_offers_to_rewrite_the_agent_config(self, world):
        world.store(enabled=True, license_key="stored-key")

        world.disable()

        world.bench.supervisor.setup_newrelic.assert_called_once_with(world.bench_path, force=False)

    def test_an_already_disabled_bench_is_reported_not_re_applied(self, world):
        world.store(enabled=False, license_key="stored-key")

        world.disable()

        assert any("already disabled" in line for line in world.prints)
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_a_never_configured_bench_is_already_disabled(self, world):
        world.disable()

        assert any("already disabled" in line for line in world.prints)
        assert world.saves == 0


class TestStatus:
    def _lines(self, world) -> list[str]:
        return [c.args[0] for c in world.output.data_raw.call_args_list if c.args]

    def test_both_halves_must_hold_to_report_as_reporting(self, world):
        world.store(enabled=True, license_key="stored-key")

        world.status()

        assert "newrelic: reporting" in self._lines(world)

    def test_enabled_without_a_key_is_not_reporting_and_says_why(self, world):
        """The state that looks fine and sends nothing: the exporter emits no env vars without a
        key, so the wrapper runs plain gunicorn."""
        world.store(enabled=True, license_key=None)

        world.status()

        lines = self._lines(world)
        assert "newrelic: not reporting" in lines
        assert any("sends nothing" in line for line in lines)

    def test_a_key_without_the_flag_is_not_reporting(self, world):
        world.store(enabled=False, license_key="stored-key")

        world.status()

        lines = self._lines(world)
        assert "newrelic: not reporting" in lines
        assert any("license key:  stored" in line for line in lines)

    def test_a_seeded_agent_config_is_reported_as_the_users(self, world):
        world.store(enabled=True, license_key="stored-key")
        world.seed_agent_config()

        world.status()

        assert any("agent config: present" in line for line in self._lines(world))

    def test_an_unseeded_agent_config_is_reported_as_absent(self, world):
        world.store(enabled=True, license_key="stored-key")

        world.status()

        assert any("agent config: not seeded" in line for line in self._lines(world))
