"""
Characterization tests for the ``fm update`` and ``fm switch``/``fm prune`` decision tables.

``fm update`` mutates a LIVE bench: it toggles developer mode, flips the environment, rewrites the
restart policy, edits the upload limit, moves Python/Node and refreshes the external database CA.
Every one of those is guarded, and several of them re-render compose files and recreate containers.
The interesting content of that module is therefore not the plumbing but the DECISION TABLE:

* which flag is refused on which runtime, and with exactly which message;
* which flag only writes config, which one re-renders compose, and which one restarts containers;
* whether the in-memory ``bench_config`` mutation is actually persisted
  (``bench_config_save``/``save_bench_config`` bookkeeping), including the paths where it is not.

Apps (``fm apps add``), admin tools (``fm tools enable``/``disable``), alias domains
(``fm domain add``/``remove``) and APM telemetry (``fm telemetry enable``/``disable``) used to live
here as flags on this command; they now have their own contract files (``test_apps_contract.py``,
``test_tools_contract.py``, ``test_domain_contract.py``, ``test_telemetry_contract.py``) and are not
re-pinned in this one. The runtime conversion (``--runtime mount``/``--runtime image``)
is still a flag on this command; its own decision table lives in
``test_update_runtime_demotion.py`` and is not re-pinned in this one either.

``fm switch``/``fm prune`` ship an already-built image. What is pinned here is how the TARGET TAG
is resolved (``--previous`` reads deploy state; ``--restore-db`` needs a recorded dump that still
exists), what is refused on a mount-runtime bench, and what the prune summary reports.

These tests describe TODAY's behaviour so the module can be refactored safely. Where the behaviour
looks wrong it is pinned as-is and called out below rather than fixed; the following suspicion
turned out to be a real defect and its pin is now inverted:
* a failed ``--node`` validation used to abort after ``--python`` had been written to the in-memory
  config and announced, but before ``save_bench_config()``, so the accepted Python change was lost;
  both requested versions are now validated before either one is written.
"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
import typer

from frappe_manager import EnableDisableOptionsEnum
from frappe_manager.commands.deploy import switch
from frappe_manager.commands.prune import prune
from frappe_manager.commands.update import update
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler
from frappe_manager.site_manager.bench_config import (
    BenchRuntime,
    FMBenchEnvType,
    RedisConfig,
    RestartPolicyEnum,
    WorkersConfig,
)
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DrainUnavailable

pytestmark = pytest.mark.timeout(15)

BENCH = "mybench.localhost"


@contextmanager
def _null_spinner(*_args, **_kwargs):
    yield


class UpdateWorld:
    """Drives the real ``update()`` with every collaborator replaced at its seam."""

    def __init__(self, tmp_path: Path, stack: ExitStack) -> None:
        self.output = MagicMock(spec=OutputHandler)
        # conftest installs a real RichOutputHandler globally; swap the INSTANCE (not the getter)
        # so the command's own get_global_output_handler() hands back an observable double.
        set_global_output_handler(self.output)

        self.benches_root = tmp_path / "benches"
        self.bench_path = self.benches_root / BENCH
        self.bench_path.mkdir(parents=True)

        self.services = MagicMock(name="services_manager")

        self.bench = MagicMock(name="Bench")
        # bench, site and domain are one string today; a mock that sets only `name` hands a
        # MagicMock to any caller that correctly asks for the site or the domain.
        self.bench.name = BENCH
        self.bench.site_name = BENCH
        self.bench.primary_domain = BENCH
        self.bench.domains = [BENCH]
        self.bench.path = self.bench_path
        self.bench.running = True

        cfg = self.bench.bench_config
        cfg.runtime = BenchRuntime.mount
        cfg.environment_type = FMBenchEnvType.dev
        cfg.restart_policy = RestartPolicyEnum.always
        cfg.admin_tools = True
        cfg.developer_mode = False
        cfg.python_version = "3.11"
        cfg.node_version = "18"
        # None so the update() no-drain warning falls back to WorkersConfig()'s default
        # kill_timeout (15s) instead of an unpredictable MagicMock repr.
        cfg.workers = None
        cfg.telemetry = None
        # A bench on fm's own redis containers, i.e. no `[redis]` table. Left as a MagicMock it
        # reads as an external redis, which is not the default a bench has.
        cfg.redis = None
        # The real payload shape, derived from the config so the redis keys the command pushes to
        # common_site_config.json are the ones the config actually holds.
        # Per SIDE, mirroring the real `get_bench_connection_config`: each absent side falls back
        # to fm's own container address, which is what makes a split (external queue, local
        # cache) a representable shape rather than a half-configured one.
        def _common_config():
            cache = (cfg.redis.cache if cfg.redis else None) or "redis://fm__mybench__redis-cache:6379"
            queue = (cfg.redis.queue if cfg.redis else None) or "redis://fm__mybench__redis-queue:6379"
            return {
                "redis_cache": cache,
                "redis_queue": queue,
                "redis_socketio": cache,
                "developer_mode": cfg.developer_mode,
            }

        cfg.get_commmon_site_config_data.side_effect = _common_config
        # The command reads telemetry through the helper and writes it back through the
        # attribute, so the double has to keep the two consistent.
        cfg.get_telemetry_config.side_effect = lambda provider="newrelic": getattr(cfg.telemetry, provider, None) if cfg.telemetry else None
        cfg.github_token = MagicMock(name="github_token")
        cfg.use_uv = True
        cfg.registry = SimpleNamespace(distribution="registry")
        cfg.export_to_compose_inputs.side_effect = dict

        self.database_config = MagicMock(name="database_config")
        cfg.get_database_config.return_value = self.database_config

        self.bench.workers.compose_file_manager.compose_path.exists.return_value = False
        self.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = True

        app_manager = self.bench.app_manager
        app_manager.bench_cli_cmd = ["/opt/bench"]
        app_manager.get_current_runtime_versions.return_value = {"python": "3.10", "node": "18"}
        app_manager.setup_python_and_node_environments.return_value = False

        bench_cls = MagicMock(name="Bench class")
        bench_cls.get_object.return_value = self.bench
        self.bench_cls = bench_cls

        self.install_site_ca = MagicMock(name="install_site_ca", return_value="/workspace/config/tls/db-ca.pem")
        self.check_migration = MagicMock(name="check_bench_migration_required")

        self.extract_python_req = MagicMock(name="extract_python_version_requirement", return_value=None)
        self.extract_node_req = MagicMock(name="extract_node_version_requirement", return_value=None)
        self.python_compat = MagicMock(name="validate_python_version_compatibility", return_value=(True, ""))
        self.node_compat = MagicMock(name="validate_node_version_compatibility", return_value=(True, ""))
        self.parse_python = MagicMock(name="parse_python_version_for_runtime", return_value=None)
        self.parse_node = MagicMock(name="parse_node_version_for_runtime", return_value=None)

        self.app_config_cls = MagicMock(name="AppConfig")
        self.app_config_cls.from_dict.side_effect = lambda data, github_token=None: SimpleNamespace(
            name=data["app"], branch=data["branch"], token=github_token
        )

        # Drain is a real collaborator now (`fm update` gates on it like `fm restart` and
        # `fm apps add`), so the harness owns it: drained by default, per-test overridable.
        self.orchestrator = MagicMock(name="DeployOrchestrator")
        self.orchestrator.drain_workers.return_value = True
        self.orchestrator.workers_config.drain_timeout = 300
        # Queue depth the redis cutover probes. A tuple is a fixed answer, a list is a sequence of
        # answers (a backlog draining), None is "could not count" -- which is NOT zero.
        self.queue_depth: object = (0, 0)

        p = stack.enter_context
        p(patch("frappe_manager.commands.update.DeployOrchestrator", return_value=self.orchestrator))

        def _depth(*_args, **_kwargs):
            if isinstance(self.queue_depth, list):
                return self.queue_depth.pop(0) if len(self.queue_depth) > 1 else self.queue_depth[0]
            return self.queue_depth

        p(patch("frappe_manager.commands.update.redis_queue_depth", _depth))
        # The wait loop must not actually sleep between polls.
        p(patch("frappe_manager.commands.update.time.sleep", lambda _seconds: None))
        p(patch("frappe_manager.commands.update.Bench", bench_cls))
        p(patch("frappe_manager.commands.update.spinner", _null_spinner))
        p(patch("frappe_manager.commands.update.AppConfig", self.app_config_cls))
        p(patch("frappe_manager.commands.update.check_bench_migration_required", self.check_migration))
        p(patch("frappe_manager.site_manager.modules.db_tls.install_site_ca", self.install_site_ca))
        # Version probing and validation live in the PLANNING module now: `fm update` decides the
        # whole change before applying any of it, so these seams moved with the decisions.
        p(patch("frappe_manager.commands.update_plan.extract_python_version_requirement", self.extract_python_req))
        p(patch("frappe_manager.commands.update_plan.extract_node_version_requirement", self.extract_node_req))
        p(patch("frappe_manager.commands.update_plan.validate_python_version_compatibility", self.python_compat))
        p(patch("frappe_manager.commands.update_plan.validate_node_version_compatibility", self.node_compat))
        p(patch("frappe_manager.commands.update_plan.parse_python_version_for_runtime", self.parse_python))
        p(patch("frappe_manager.commands.update_plan.parse_node_version_for_runtime", self.parse_node))

    # -- knobs -------------------------------------------------------------

    @property
    def config(self):
        return self.bench.bench_config

    def make_frappe_app_dir(self) -> Path:
        path = self.bench_path / "workspace" / "frappe-bench" / "apps" / "frappe"
        path.mkdir(parents=True)
        return path

    def write_apps_txt(self, apps: list[str]) -> Path:
        path = self.bench_path / "workspace" / "frappe-bench" / "sites" / "apps.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(apps) + "\n")
        return path

    # -- observation -------------------------------------------------------

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
    def heads(self) -> list[str]:
        return [c.args[0] for c in self.output.change_head.call_args_list if c.args]

    @property
    def compose_up_calls(self) -> list:
        return self.bench.docker_client.compose.up.call_args_list

    @property
    def saves(self) -> int:
        return self.bench.save_bench_config.call_count

    # -- run ---------------------------------------------------------------

    def run(self, *, site: str | None = None, **kwargs):
        ctx = MagicMock(spec=typer.Context)
        # `fm update BENCH/SITE` parses to a bench plus the addressed site; nothing in update()
        # reads it today (--db-ca is the one remaining Site Option and always targets the bench's
        # primary site), but the address grammar still accepts and stashes it like every other
        # BenchSiteArgument command.
        ctx.obj = {"services": self.services, "site": site}
        return update(ctx, address=BENCH, **kwargs)


@pytest.fixture
def world(tmp_path):
    with ExitStack() as stack:
        yield UpdateWorld(tmp_path, stack)


IMMUTABLE_REFUSAL = (
    f"{BENCH} is image runtime; code, apps, Python/Node and developer mode are immutable -- "
    "ship changes with 'fm bake' then 'fm switch', install apps with 'fm apps add', or demote to "
    f"an editable workspace first with 'fm update {BENCH} --runtime mount'. "
    "'fm update' on an image bench still changes environment, restart policy and the database CA, "
    "and APM is 'fm telemetry enable'."
)


class TestImageRuntimeImmutabilityGate:
    """Which flags an image-runtime bench refuses, and what it still accepts."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"python_version": "3.12"}, id="python"),
            pytest.param({"node_version": "20"}, id="node"),
            pytest.param({"developer_mode": EnableDisableOptionsEnum.enable}, id="developer-mode-enable"),
        ],
    )
    def test_code_affecting_flags_are_refused_on_image_runtime(self, world, kwargs):
        world.config.runtime = BenchRuntime.image

        with pytest.raises(typer.Exit) as exc:
            world.run(**kwargs)

        assert exc.value.exit_code == 1
        assert world.errors == [IMMUTABLE_REFUSAL]
        assert world.saves == 0

    def test_developer_mode_disable_is_not_immutable_on_image_runtime(self, world):
        """Only ENABLE writes app files; disabling is a settings-only change and is allowed."""
        world.config.runtime = BenchRuntime.image
        world.config.developer_mode = True

        world.run(developer_mode=EnableDisableOptionsEnum.disable)

        assert world.errors == []
        assert world.config.developer_mode is False
        world.bench.set_common_bench_config.assert_called_once_with({"developer_mode": False})

    def test_settings_only_flags_are_allowed_on_image_runtime(self, world):
        world.config.runtime = BenchRuntime.image

        world.run(upload_limit="100M")

        assert world.errors == []
        world.bench.update_upload_limit.assert_called_once_with("100M")

    def test_mount_runtime_never_hits_the_gate(self, world):
        world.run(python_version="3.12")

        assert world.errors == []
        assert world.config.python_version == "3.12"

    def test_immutability_gate_precedes_the_running_check(self, world):
        """A stopped IMAGE bench asked for a code change reports immutability, not BenchNotRunning."""
        world.config.runtime = BenchRuntime.image
        world.bench.running = False

        with pytest.raises(typer.Exit):
            world.run(python_version="3.12")

        assert world.errors == [IMMUTABLE_REFUSAL]


class TestBenchMustBeRunning:
    def test_stopped_bench_refuses_every_update(self, world):
        world.bench.running = False

        with pytest.raises(BenchNotRunning) as exc:
            world.run(upload_limit="100M")

        assert exc.value.bench_name == BENCH
        world.bench.update_upload_limit.assert_not_called()
        assert world.saves == 0


NO_DATABASE_REFUSAL = (
    f"{BENCH} has no \\[database] entry in bench_config.toml: the bench uses the fm-managed "
    "'mariadb' container, whose TLS material fm owns, so there is no external CA to refresh."
)


class TestExternalDatabaseCaRefresh:
    """``--db-ca``: three writes, no restart, and the refusal for fm-managed databases."""

    def test_refused_when_the_bench_has_no_external_database(self, world, tmp_path):
        world.config.get_database_config.return_value = None
        ca = tmp_path / "ca.pem"
        ca.write_text("ca")

        with pytest.raises(typer.Exit) as exc:
            world.run(db_ca=ca)

        assert exc.value.exit_code == 1
        assert world.errors == [NO_DATABASE_REFUSAL]
        world.install_site_ca.assert_not_called()
        assert world.saves == 0

    def test_installs_the_ca_and_records_the_unresolved_host_path(self, world, tmp_path):
        """The recorded path is absolute but deliberately NOT resolved: symlink rotation must stick."""
        real = tmp_path / "archive" / "ca-2026.pem"
        real.parent.mkdir()
        real.write_text("ca")
        link = tmp_path / "live" / "ca.pem"
        link.parent.mkdir()
        link.symlink_to(real)

        world.run(db_ca=link)

        world.install_site_ca.assert_called_once_with(world.bench_path, BENCH, link)
        assert world.database_config.ca == str(link.absolute())
        assert world.database_config.ca != str(real)
        assert world.saves == 1

    def test_success_reports_both_writes_and_promises_no_restart(self, world, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("ca")

        world.run(db_ca=ca)

        assert (
            "Installed CA at /workspace/config/tls/db-ca.pem and rebuilt the bench ca-bundle.pem" in world.prints
        )
        assert (
            "Running containers read the new CA on their next database connection; no restart needed."
            in world.prints
        )
        assert world.compose_up_calls == []
        world.bench.restart_web_containers_services.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(OSError("permission denied writing ca-bundle.pem"), id="oserror"),
            pytest.param(ValueError("not a PEM certificate"), id="valueerror"),
        ],
    )
    def test_install_failure_aborts_without_recording_the_path(self, world, tmp_path, error):
        ca = tmp_path / "ca.pem"
        ca.write_text("ca")
        world.install_site_ca.side_effect = error

        with pytest.raises(typer.Exit) as exc:
            world.run(db_ca=ca)

        assert exc.value.exit_code == 1
        assert world.errors == [str(error)]
        assert world.saves == 0

    def test_a_site_that_never_had_tls_is_told_frappe_still_connects_without_it(self, world, tmp_path):
        """``db_ssl_ca`` reaches ``sites/<site>/site_config.json`` only from ``[database.<site>].ca`` at
        create time and update rewrites no site config, so on a site configured without TLS the driver
        keeps sending none -- the 'no restart needed' reassurance would claim a CA that is not in play."""
        world.database_config.ca = None
        ca = tmp_path / "ca.pem"
        ca.write_text("ca")

        world.run(db_ca=ca)

        no_restart = "Running containers read the new CA on their next database connection; no restart needed."
        assert no_restart not in world.prints
        assert len(world.warnings) == 1
        assert "carries no db_ssl_ca" in world.warnings[0]
        assert "WITHOUT TLS" in world.warnings[0]
        assert world.database_config.ca == str(ca.absolute())
        assert world.saves == 1


class TestDeveloperMode:
    @pytest.mark.parametrize(
        ("option", "expected"),
        [
            pytest.param(EnableDisableOptionsEnum.enable, True, id="enable"),
            pytest.param(EnableDisableOptionsEnum.disable, False, id="disable"),
        ],
    )
    def test_toggle_writes_common_site_config_and_persists(self, world, option, expected):
        world.config.developer_mode = not expected

        world.run(developer_mode=option)

        assert world.config.developer_mode is expected
        world.bench.set_common_bench_config.assert_called_once_with({"developer_mode": expected})
        assert world.saves == 1
        assert world.compose_up_calls == []


class TestEnvironmentSwitch:
    def test_rerenders_compose_with_frappe_env_and_recreates_frappe_only(self, world):
        world.config.export_to_compose_inputs.side_effect = lambda: {"environment": {"frappe": {"KEEP": "1"}}}

        world.run(environment=FMBenchEnvType.prod)

        assert world.config.environment_type == FMBenchEnvType.prod
        rendered = world.bench.generate_compose.call_args.args[0]
        assert rendered["environment"]["frappe"] == {"KEEP": "1", "FRAPPE_ENV": "prod"}
        assert world.compose_up_calls[0].kwargs == {"services": ["frappe"], "detach": True, "force_recreate": True}
        assert len(world.compose_up_calls) == 1
        assert world.saves == 1

    def test_admin_tools_and_developer_mode_are_left_alone(self, world):
        """Admin tools and developer mode are decided at create time and never revisited here."""
        world.run(environment=FMBenchEnvType.prod)

        world.bench.admin_tools.enable.assert_not_called()
        world.bench.admin_tools.disable.assert_not_called()
        world.bench.sync_admin_tools_compose.assert_not_called()
        world.bench.set_common_bench_config.assert_not_called()

    def test_the_option_help_promises_only_what_the_branch_does(self, world):
        """The help advertised 'adjusts FRAPPE_ENV, serving mode and admin-tool defaults'; the two tests
        above pin that nothing but FRAPPE_ENV and the frappe container is touched, so the promise of
        admin-tool defaults sent operators looking for a switch that does not exist."""
        option = update.__annotations__["environment"].__metadata__[0]

        assert "admin-tool defaults" not in option.help
        assert "fm tools" in option.help
        assert "FRAPPE_ENV" in option.help


class TestRestartPolicy:
    def test_unchanged_policy_touches_nothing(self, world):
        """An already-satisfied request is reported as nothing to do, not reapplied."""
        world.run(restart_policy=RestartPolicyEnum.always)

        assert world.prints == ["mybench.localhost: nothing to do (restart policy is already 'always')"]
        world.bench.generate_compose.assert_not_called()
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_changed_policy_rerenders_every_compose_file_and_recreates_containers(self, world):
        world.bench.workers.compose_file_manager.compose_path.exists.return_value = True

        world.run(restart_policy=RestartPolicyEnum.unless_stopped)

        assert world.config.restart_policy == RestartPolicyEnum.unless_stopped
        world.bench.generate_compose.assert_called_once()
        world.bench.workers.generate_compose.assert_called_once_with()
        world.bench.admin_tools.generate_compose.assert_called_once_with()
        assert world.compose_up_calls[0].kwargs == {"detach": True, "force_recreate": True}
        assert world.saves == 1

    def test_absent_optional_compose_files_are_skipped(self, world):
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False

        world.run(restart_policy=RestartPolicyEnum.unless_stopped)

        world.bench.workers.generate_compose.assert_not_called()
        world.bench.admin_tools.generate_compose.assert_not_called()

    def test_no_restart_on_production_warns_before_it_is_applied(self, world):
        """The warning is part of the PLAN, so `--dry-run` shows it before anything changes."""
        world.config.environment_type = FMBenchEnvType.prod

        world.run(restart_policy=RestartPolicyEnum.no)

        assert world.warnings == [
            "restart policy 'no' on a production bench: containers will not auto-recover "
            "from failures or system reboots",
        ]
        assert world.config.restart_policy == RestartPolicyEnum.no

    def test_no_restart_on_development_does_not_warn(self, world):
        world.run(restart_policy=RestartPolicyEnum.no)

        assert world.warnings == []

    def test_the_worker_and_tools_projects_are_recreated_too(self, world):
        """Workers and admin tools are SEPARATE compose projects with their own DockerClient. Bringing
        up the bench project alone leaves them running under the old policy on the daemon while
        bench_config.toml and the rendered compose files claim the new one."""
        world.bench.workers.compose_file_manager.compose_path.exists.return_value = True

        world.run(restart_policy=RestartPolicyEnum.unless_stopped)

        world.bench.workers.docker_client.compose.up.assert_called_once_with(
            services=[], detach=True, force_recreate=True, pull="never"
        )
        world.bench.admin_tools.enable.assert_called_once_with(force_recreate_container=True)

    def test_absent_optional_compose_files_are_not_recreated(self, world):
        world.bench.workers.compose_file_manager.compose_path.exists.return_value = False
        world.bench.admin_tools.compose_file_manager.compose_path.exists.return_value = False

        world.run(restart_policy=RestartPolicyEnum.unless_stopped)

        world.bench.workers.docker_client.compose.up.assert_not_called()
        world.bench.admin_tools.enable.assert_not_called()

    def test_an_unchanged_policy_recreates_nothing_anywhere(self, world):
        world.bench.workers.compose_file_manager.compose_path.exists.return_value = True

        world.run(restart_policy=RestartPolicyEnum.always)

        world.bench.workers.docker_client.compose.up.assert_not_called()
        world.bench.admin_tools.enable.assert_not_called()


class TestMigrationGate:
    """``fm update`` is the largest mutator in the product; the group-callback gate cannot see the
    subcommand's benchname, so the command must run the per-bench gate itself before it loads and
    rewrites an old-schema bench_config.toml with the current model."""

    def test_the_gate_runs_with_the_benchname_before_the_bench_is_loaded(self, world):
        order = []
        world.check_migration.side_effect = lambda name: order.append(("gate", name))
        world.bench_cls.get_object.side_effect = lambda *_a, **_kw: order.append("load") or world.bench

        world.run(upload_limit="100M")

        assert order == [("gate", BENCH), "load"]

    def test_a_bench_needing_migration_aborts_before_anything_is_touched(self, world):
        world.check_migration.side_effect = typer.Exit(1)

        with pytest.raises(typer.Exit):
            world.run(upload_limit="100M")

        world.bench_cls.get_object.assert_not_called()
        world.bench.update_upload_limit.assert_not_called()


class TestUploadLimit:
    def test_delegates_to_the_bench_and_saves_nothing(self, world):
        """`update_upload_limit` owns its own save, so apply must not write the file a second time."""
        world.run(upload_limit="500M")

        world.bench.update_upload_limit.assert_called_once_with("500M")
        assert world.saves == 0
        assert world.compose_up_calls == []

    def test_an_unchanged_limit_is_not_reapplied(self, world):
        """Reapplying rewrote the nginx confs and reloaded the proxy to arrive at the same value."""
        world.config.upload_limit = "500M"

        world.run(upload_limit="500m")

        world.bench.update_upload_limit.assert_not_called()
        assert world.saves == 0

    def test_a_malformed_limit_is_refused_before_any_flag_is_applied(self, world):
        """THE bug this split fixes. The format check used to live inside `update_upload_limit`,
        so it fired mid-table: `-e prod --upload-limit BOGUS` exited 1 having already recreated
        the frappe container as prod, while bench_config.toml still said dev and `fm info` still
        reported dev -- and `republish_site_map` re-injects FRAPPE_ENV from that file, so a later
        unrelated command silently flipped serving back."""
        with pytest.raises(typer.BadParameter) as exc:
            world.run(environment=FMBenchEnvType.prod, upload_limit="BOGUS")

        assert "Invalid upload limit format" in exc.value.message
        assert world.config.environment_type == FMBenchEnvType.dev
        world.bench.generate_compose.assert_not_called()
        world.bench.update_upload_limit.assert_not_called()
        assert world.compose_up_calls == []
        assert world.saves == 0



class TestPythonAndNodeVersions:
    def test_validation_is_skipped_when_frappe_is_not_on_disk(self, world):
        world.run(python_version="3.12")

        world.extract_python_req.assert_not_called()
        world.python_compat.assert_not_called()
        assert world.config.python_version == "3.12"
        world.bench.app_manager.setup_python_and_node_environments.assert_called_once_with(
            use_run=True, recreate_python_env=True
        )

    def test_incompatible_python_is_refused_with_a_hint(self, world):
        world.make_frappe_app_dir()
        world.extract_python_req.return_value = ">=3.11,<3.13"
        world.python_compat.return_value = (False, "Python 3.9 does not satisfy >=3.11,<3.13")
        world.parse_python.return_value = "3.12"

        with pytest.raises(typer.Exit) as exc:
            world.run(python_version="3.9")

        assert exc.value.exit_code == 1
        assert world.config.python_version == "3.11"
        assert world.output.display_error.call_args.args[0] == "Python 3.9 does not satisfy >=3.11,<3.13"
        assert world.output.display_error.call_args.kwargs == {"emoji_code": ":cross_mark:"}
        assert "Hint: Try --python 3.12" in world.prints
        assert "Use --skip-version-check to bypass this validation (not recommended)" in world.prints
        world.bench.app_manager.setup_python_and_node_environments.assert_not_called()
        assert world.saves == 0

    def test_no_hint_is_offered_when_the_requirement_yields_no_runtime_version(self, world):
        world.make_frappe_app_dir()
        world.extract_python_req.return_value = "weird-spec"
        world.python_compat.return_value = (False, "incompatible")
        world.parse_python.return_value = None

        with pytest.raises(typer.Exit):
            world.run(python_version="3.9")

        assert not [line for line in world.prints if line.startswith("Hint:")]

    def test_skip_version_check_downgrades_the_refusal_to_a_warning(self, world):
        world.make_frappe_app_dir()
        world.extract_python_req.return_value = ">=3.11,<3.13"
        world.python_compat.return_value = (False, "nope")
        world.parse_python.return_value = "3.12"

        world.run(python_version="3.9", skip_version_check=True)

        # Warned in the PLAN, so `--dry-run` shows the incompatibility before the venv is rebuilt.
        assert world.warnings == [
            "Python 3.9 is incompatible with frappe's requirement >=3.11,<3.13",
            "consider --python 3.12 instead",
        ]
        assert world.config.python_version == "3.9"
        world.bench.app_manager.setup_python_and_node_environments.assert_called_once()

    def test_an_unrecorded_version_is_reported_as_not_set(self, world):
        """The plan names the RECORDED value it is changing, and renders its absence readably."""
        world.config.python_version = None

        world.run(python_version="3.12")

        assert "  python  not set -> 3.12" in world.prints

    def test_the_installed_runtime_is_probed_once_for_validation(self, world):
        world.run(python_version="3.12")

        world.bench.app_manager.get_current_runtime_versions.assert_called_once_with(use_run=True)

    def test_an_unchanged_version_does_no_work_at_all(self, world):
        """Measured at 116s on a real bench, ending in an UNDRAINED worker restart, to arrive
        where the bench already was: fm itself reported "already satisfies ... skipping
        installation" and then restarted frappe, socketio, schedule and both workers."""
        world.config.python_version = "3.11"

        world.run(python_version="3.11")

        world.bench.app_manager.setup_python_and_node_environments.assert_not_called()
        world.bench.restart_web_containers_services.assert_not_called()
        world.bench.restart_workers_containers_services.assert_not_called()
        assert world.saves == 0

    def test_incompatible_node_is_refused_with_a_hint(self, world):
        world.make_frappe_app_dir()
        world.extract_node_req.return_value = ">=18"
        world.node_compat.return_value = (False, "Node 16 does not satisfy >=18")
        world.parse_node.return_value = "18"

        with pytest.raises(typer.Exit) as exc:
            world.run(node_version="16")

        assert exc.value.exit_code == 1
        assert world.config.node_version == "18"
        assert "Hint: Try --node 18" in world.prints
        world.bench.app_manager.setup_python_and_node_environments.assert_not_called()

    def test_a_node_refusal_leaves_the_python_change_of_the_same_run_unapplied(self, world):
        """Was pinned as a suspicion (``test_a_node_refusal_discards_the_python_change_of_the_same_run``)
        and confirmed as a real defect: python was written to the in-memory config and announced as
        updated, then the node refusal exited before save_bench_config(), so the accepted half of the
        request was reported as done and silently dropped. Both requested versions are now validated
        before either is written, so the assertions below are the inverse of the old pin."""
        world.make_frappe_app_dir()
        world.extract_node_req.return_value = ">=18"
        world.node_compat.return_value = (False, "Node 16 does not satisfy >=18")

        with pytest.raises(typer.Exit):
            world.run(python_version="3.12", node_version="16")

        assert world.config.python_version == "3.11"
        assert not [line for line in world.prints if line.startswith("Python:")]
        assert "Updating Python version" not in world.heads
        assert world.saves == 0

    def test_skip_version_check_downgrades_the_node_refusal_to_a_warning(self, world):
        world.make_frappe_app_dir()
        world.extract_node_req.return_value = ">=18"
        world.node_compat.return_value = (False, "nope")
        world.parse_node.return_value = "20"

        world.run(node_version="16", skip_version_check=True)

        assert world.warnings == [
            "Node 16 is incompatible with frappe's requirement >=18",
            "consider --node 20 instead",
        ]
        assert world.config.node_version == "16"
        world.bench.app_manager.setup_python_and_node_environments.assert_called_once()

    def test_runtime_change_persists_before_touching_the_environment(self, world):
        world.run(python_version="3.12", node_version="20")

        assert (world.config.python_version, world.config.node_version) == ("3.12", "20")
        assert world.saves == 1
        world.bench.restart_web_containers_services.assert_called_once_with(use_container_restart=False)
        world.bench.restart_workers_containers_services.assert_called_once_with(use_container_restart=False)

    def test_no_recreate_python_env_is_forwarded(self, world):
        world.run(python_version="3.12", recreate_python_env=False)

        world.bench.app_manager.setup_python_and_node_environments.assert_called_once_with(
            use_run=True, recreate_python_env=False
        )
        world.bench.app_manager.install_apps.assert_not_called()

    def test_a_recreated_venv_reinstalls_every_app_listed_in_apps_txt(self, world):
        world.write_apps_txt(["frappe", "erpnext", ""])
        world.bench.app_manager.setup_python_and_node_environments.return_value = True

        world.run(python_version="3.12")

        assert "Found 2 installed apps: frappe, erpnext" in world.prints
        kwargs = world.bench.app_manager.install_apps.call_args.kwargs
        assert [app.name for app in kwargs["apps_list"]] == ["frappe", "erpnext"]
        assert kwargs["github_token"] is world.config.github_token
        assert kwargs["use_uv"] is True
        assert kwargs["skip_clone"] is True
        assert kwargs["use_run"] is True

    def test_a_recreated_venv_without_apps_txt_warns_and_reinstalls_nothing(self, world):
        world.bench.app_manager.setup_python_and_node_environments.return_value = True

        world.run(python_version="3.12")

        assert world.warnings == ["No apps.txt found, skipping app reinstallation"]
        world.bench.app_manager.install_apps.assert_not_called()

    def test_apps_are_not_reinstalled_when_the_venv_was_kept(self, world):
        world.write_apps_txt(["frappe"])
        world.bench.app_manager.setup_python_and_node_environments.return_value = False

        world.run(python_version="3.12")

        world.bench.app_manager.install_apps.assert_not_called()
        world.bench.restart_web_containers_services.assert_called_once()


class TestNoOptions:
    def test_an_update_with_no_flags_changes_nothing(self, world):
        world.run()

        assert world.saves == 0
        assert world.compose_up_calls == []
        world.bench.generate_compose.assert_not_called()
        assert world.errors == []


# ---------------------------------------------------------------------------
# fm switch / fm prune
# ---------------------------------------------------------------------------

NOT_IMAGE_RUNTIME_REFUSAL = (
    f"Bench '{BENCH}' is not in image runtime. To convert it: set runtime = 'image' "
    f"and a top-level image in its bench_config.toml, then re-run "
    f"fm switch {BENCH} <repo:tag> -- the switch migrates the existing site onto the "
    f"baked image (site data and DB carry over)."
)


class DeployWorld:
    """Drives the real ``switch()``/``prune()`` with orchestration mocked."""

    def __init__(self, tmp_path: Path, stack: ExitStack) -> None:
        self.output = MagicMock(spec=OutputHandler)
        set_global_output_handler(self.output)

        self.tmp_path = tmp_path

        self.services = MagicMock(name="services_manager")

        self.bench = MagicMock(name="Bench")
        # bench, site and domain are one string today; a mock that sets only `name` hands a
        # MagicMock to any caller that correctly asks for the site or the domain.
        self.bench.name = BENCH
        self.bench.site_name = BENCH
        self.bench.primary_domain = BENCH
        self.bench.domains = [BENCH]
        self.bench.path = tmp_path / "bench"
        cfg = self.bench.bench_config
        cfg.runtime = BenchRuntime.image
        cfg.deploy_state = None
        # None -> the resolver falls through to the host [prune] table (a MagicMock here
        # would reach int()/parse_size() and blow up for the wrong reason).
        cfg.prune = None

        from frappe_manager.metadata_manager import FMPruneConfig

        self.fm_config = MagicMock(name="fm_config_manager")
        self.fm_config.prune = FMPruneConfig()

        self.bench_cls = MagicMock(name="Bench class")
        self.bench_cls.get_object.return_value = self.bench

        self.orchestrator = MagicMock(name="orchestrator")
        self.orchestrator_cls = MagicMock(name="DeployOrchestrator", return_value=self.orchestrator)

        p = stack.enter_context
        p(patch("frappe_manager.commands.deploy.Bench", self.bench_cls))
        p(patch("frappe_manager.commands.deploy.DeployOrchestrator", self.orchestrator_cls))
        p(patch("frappe_manager.commands.prune.Bench", self.bench_cls))
        # fm prune imports the orchestrator from its home module at call time.
        p(patch("frappe_manager.site_manager.modules.deploy_orchestrator.DeployOrchestrator", self.orchestrator_cls))

    @property
    def config(self):
        return self.bench.bench_config

    @property
    def errors(self) -> list[str]:
        return [c.args[0] for c in self.output.display_error.call_args_list if c.args]

    @property
    def prints(self) -> list[str]:
        return [c.args[0] for c in self.output.print.call_args_list if c.args]

    def _ctx(self):
        ctx = MagicMock(spec=typer.Context)
        ctx.obj = {"services": self.services, "fm_config_manager": self.fm_config}
        return ctx

    def switch(self, **kwargs):
        return switch(self._ctx(), benchname=BENCH, **kwargs)

    def prune(self, **kwargs):
        return prune(self._ctx(), benchname=BENCH, **kwargs)


@pytest.fixture
def ship(tmp_path):
    with ExitStack() as stack:
        yield DeployWorld(tmp_path, stack)


class TestMountRuntimeIsRefused:
    def test_switch_refuses_a_mount_runtime_bench_before_resolving_an_image(self, ship):
        ship.config.runtime = BenchRuntime.mount

        with pytest.raises(typer.Exit) as exc:
            ship.switch(image="local/mybench:t1")

        assert exc.value.exit_code == 1
        assert ship.errors == [NOT_IMAGE_RUNTIME_REFUSAL]
        ship.orchestrator_cls.assert_not_called()


SHOP = "shop.mybench.localhost"


def _deploy_state(current="local/mybench:t2", previous="local/mybench:t1", backups=None):
    if backups is None:
        backups = {BENCH: "/b/db.sql"}
    return SimpleNamespace(
        current_image=current,
        previous_image=previous,
        history=[SimpleNamespace(image=current, backups=backups)],
    )


class TestSwitchTargetImageResolution:
    def test_an_explicit_image_is_deployed_as_given(self, ship):
        ship.config.deploy_state = _deploy_state()

        ship.switch(image="local/mybench:t9")

        assert ship.orchestrator.deploy.call_args.args == ("local/mybench:t9",)
        assert ship.orchestrator.deploy.call_args.kwargs == {
            "rolling": None,
            "migrate_override": None,
            "restore_db_dumps": {},
            "prune_keep": None,
            # False because no --yes was passed: a restore that would overwrite fm's own mariadb
            # has to be confirmed, not just requested.
            "restore_confirmed": False,
        }

    def test_rolling_and_keep_are_forwarded_to_the_orchestrator(self, ship):
        ship.config.deploy_state = _deploy_state()

        ship.switch(image="local/mybench:t9", rolling=False, keep=3)

        assert ship.orchestrator.deploy.call_args.kwargs["rolling"] is False
        assert ship.orchestrator.deploy.call_args.kwargs["prune_keep"] == 3

    def test_a_resolution_failure_is_surfaced_verbatim(self, ship):
        ship.config.deploy_state = _deploy_state()

        with pytest.raises(typer.Exit) as exc:
            ship.switch()

        assert exc.value.exit_code == 1
        assert ship.errors == ["Missing target: pass an image reference or --previous."]
        ship.orchestrator_cls.assert_not_called()

    def test_previous_rolls_back_and_disables_migrate_by_default(self, ship):
        ship.config.deploy_state = _deploy_state()

        ship.switch(previous=True)

        assert ship.orchestrator.deploy.call_args.args == ("local/mybench:t1",)
        assert ship.orchestrator.deploy.call_args.kwargs["migrate_override"] is False
        assert "Rollback: migrate disabled for this run (override with --migrate)." in ship.prints

    def test_an_explicit_migrate_flag_survives_a_rollback(self, ship):
        ship.config.deploy_state = _deploy_state()

        ship.switch(previous=True, migrate=True)

        assert ship.orchestrator.deploy.call_args.kwargs["migrate_override"] is True
        assert ship.prints == []

    def test_restore_db_passes_every_recorded_dump_when_they_all_exist(self, ship):
        # One dump per SITE: each site has its own schema, so a rollback that carried only the
        # primary's dump would leave the rest migrated against the code being rolled back.
        primary = ship.tmp_path / "db-primary.sql"
        shop = ship.tmp_path / "db-shop.sql"
        primary.write_text("dump")
        shop.write_text("dump")
        ship.config.deploy_state = _deploy_state(backups={BENCH: str(primary), SHOP: str(shop)})

        ship.switch(image="local/mybench:t9", restore_db=True)

        assert ship.orchestrator.deploy.call_args.kwargs["restore_db_dumps"] == {BENCH: primary, SHOP: shop}

    def test_restore_db_refuses_when_the_recorded_dump_is_gone(self, ship):
        ship.config.deploy_state = _deploy_state(backups={BENCH: "/b/vanished.sql"})

        with pytest.raises(typer.Exit) as exc:
            ship.switch(image="local/mybench:t9", restore_db=True)

        assert exc.value.exit_code == 1
        assert ship.errors == ["Recorded DB backup(s) missing on disk: /b/vanished.sql"]
        ship.orchestrator_cls.assert_not_called()

    def test_restore_db_is_all_or_nothing_when_one_site_dump_is_gone(self, ship):
        # Restoring the sites whose dumps survive would leave the bench split across two points
        # in time. The refusal names what is missing, and nothing is deployed.
        primary = ship.tmp_path / "db-primary.sql"
        primary.write_text("dump")
        ship.config.deploy_state = _deploy_state(backups={BENCH: str(primary), SHOP: "/b/vanished-shop.sql"})

        with pytest.raises(typer.Exit) as exc:
            ship.switch(image="local/mybench:t9", restore_db=True)

        assert exc.value.exit_code == 1
        assert ship.errors == ["Recorded DB backup(s) missing on disk: /b/vanished-shop.sql"]
        ship.orchestrator_cls.assert_not_called()

    def test_restore_db_refuses_when_no_dump_was_recorded(self, ship):
        state = _deploy_state()
        state.history = []
        ship.config.deploy_state = state

        with pytest.raises(typer.Exit) as exc:
            ship.switch(image="local/mybench:t9", restore_db=True)

        assert exc.value.exit_code == 1
        assert ship.errors == [
            "No DB backup recorded for the current deploy (local/mybench:t2). "
            "Dumps live under <bench>/backups/deploy-*/ -- restore manually if one exists."
        ]

    def test_a_deploy_failure_during_switch_is_reported_as_exit_1(self, ship):
        ship.config.deploy_state = _deploy_state()
        ship.orchestrator.deploy.side_effect = DeployError("swap failed")

        with pytest.raises(typer.Exit) as exc:
            ship.switch(image="local/mybench:t9")

        assert exc.value.exit_code == 1
        assert ship.errors == ["swap failed"]


class TestPrune:
    """``fm prune --only releases``: what the releases category reports and refuses.

    The command grew backups/logs categories (tests in this class narrow with --only so the
    releases contract stays isolated), and a mount-runtime bench is no longer REFUSED: the
    other categories apply to it, so releases just reports itself inapplicable.
    """

    @staticmethod
    def _releases_only(ship, **kwargs):
        from frappe_manager.commands.prune import PruneCategory

        return ship.prune(only=[PruneCategory.releases], **kwargs)

    def test_a_mount_runtime_bench_skips_releases_instead_of_refusing(self, ship):
        ship.config.runtime = BenchRuntime.mount

        self._releases_only(ship)

        assert ship.errors == []
        assert ship.prints == ["Releases : not an image-runtime bench, nothing to prune"]
        ship.orchestrator_cls.assert_not_called()

    def test_nothing_to_prune_reports_the_retained_count(self, ship):
        ship.orchestrator.prune_releases.return_value = {"entries": 0, "kept": 4, "backups": [], "images": []}

        self._releases_only(ship)

        # Plan-first: the planning pass is a dry_run call; nothing to do means no second call
        # and no prompt.
        ship.orchestrator.prune_releases.assert_called_once_with(keep=None, dry_run=True)
        ship.output.prompt_ask.assert_not_called()
        assert ship.prints == ["Releases : nothing to prune (4 release(s) recorded, all within retention)"]

    def test_dry_run_lists_every_backup_dir_and_image(self, ship):
        ship.orchestrator.prune_releases.return_value = {
            "entries": 2,
            "kept": 3,
            "backups": ["/b/deploy-1", "/b/deploy-2"],
            "images": ["local/mybench:t1"],
        }

        self._releases_only(ship, keep_releases=3, dry_run=True)

        ship.orchestrator.prune_releases.assert_called_once_with(keep=3, dry_run=True)
        assert ship.prints == [
            "Releases : would prune 2 release(s), keep 3 · 2 backup dir(s) · 1 image(s)",
            "backup dir  /b/deploy-1",
            "backup dir  /b/deploy-2",
            "image       local/mybench:t1",
        ]

    def test_a_real_prune_shows_the_plan_then_executes_on_yes(self, ship):
        """Plan-first: the listing (with 'would') is printed BEFORE anything happens, and
        --yes skips the prompt; execution is a second prune_releases call without dry_run."""
        ship.orchestrator.prune_releases.return_value = {
            "entries": 2,
            "kept": 3,
            "backups": ["/b/deploy-1"],
            "images": ["local/mybench:t1"],
        }

        self._releases_only(ship, keep_releases=3, yes=True)

        assert ship.orchestrator.prune_releases.call_args_list == [
            call(keep=3, dry_run=True),
            call(keep=3, dry_run=False),
        ]
        ship.output.prompt_ask.assert_not_called()
        assert ship.prints[0] == "Releases : would prune 2 release(s), keep 3 · 1 backup dir(s) · 1 image(s)"
        assert ship.prints[-1].startswith("Done     :")

    def test_without_yes_the_prompt_gates_execution_and_default_no_aborts(self, ship):
        ship.orchestrator.prune_releases.return_value = {
            "entries": 2,
            "kept": 3,
            "backups": ["/b/deploy-1"],
            "images": [],
        }
        ship.output.prompt_ask.return_value = "no"

        self._releases_only(ship, keep_releases=3)

        kwargs = ship.output.prompt_ask.call_args.kwargs
        assert kwargs["default"] == "no"
        assert kwargs["required_flag"] == "--yes"
        # Only the planning call happened; nothing was executed.
        ship.orchestrator.prune_releases.assert_called_once_with(keep=3, dry_run=True)
        assert ship.prints[-1] == "Aborted; nothing touched."

    def test_a_prune_failure_is_reported_as_exit_1(self, ship):
        ship.orchestrator.prune_releases.side_effect = DeployError("history unreadable")

        with pytest.raises(typer.Exit) as exc:
            self._releases_only(ship)

        assert exc.value.exit_code == 1
        assert ship.errors == ["history unreadable"]


class TestPruneAllCategories:
    """The default run covers all three categories and totals what it reclaimed."""

    def test_default_run_reports_every_category(self, ship):
        ship.orchestrator.prune_releases.return_value = {"entries": 0, "kept": 4, "backups": [], "images": []}

        ship.prune(dry_run=True)

        assert ship.prints == [
            "Releases : nothing to prune (4 release(s) recorded, all within retention)",
            "Backups  : nothing beyond retention",
            "Logs     : nothing over the rotation threshold",
        ]

    def test_backups_category_removes_stale_sessions_and_reports_size(self, ship):
        import os
        import time

        from frappe_manager.commands.prune import PruneCategory

        root = ship.bench.path / "backups" / "migrations"
        root.mkdir(parents=True)
        now = time.time()
        for i in range(5):
            d = root / f"s{i}"
            d.mkdir()
            (d / "f").write_bytes(b"x" * 100)
            os.utime(d, (now - (5 - i) * 60, now - (5 - i) * 60))

        ship.prune(only=[PruneCategory.backups], yes=True)

        assert sorted(p.name for p in root.iterdir()) == ["s2", "s3", "s4"]  # newest 3 kept
        assert any(p.startswith("Backups  : would remove migrations: 2 session(s) beyond keep 3") for p in ship.prints)
        assert any(p.startswith("Done     :") for p in ship.prints)
        assert any(p.startswith("Total    :") for p in ship.prints)  # reclaimable, in the plan

    def test_logs_category_rotates_in_place_and_keeps_the_inode(self, ship):
        from frappe_manager.commands.prune import PruneCategory

        logs_dir = ship.bench.path / "workspace" / "frappe-bench" / "logs"
        logs_dir.mkdir(parents=True)
        big = logs_dir / "worker.error.log"
        big.write_bytes(b"x" * 2048)
        small = logs_dir / "web.log"
        small.write_bytes(b"y" * 10)
        inode = big.stat().st_ino

        ship.prune(only=[PruneCategory.logs], rotate_over="1K", yes=True)

        assert big.stat().st_size == 0  # truncated in place...
        assert big.stat().st_ino == inode  # ...same inode: the writers' fd stays valid
        assert small.stat().st_size == 10  # under the threshold, untouched
        archives = list(logs_dir.glob("worker.error.log.*.gz"))
        assert len(archives) == 1
        import gzip

        with gzip.open(archives[0], "rb") as f:
            assert f.read() == b"x" * 2048


KEEP_FLOOR_REFUSAL = "--keep must be at least 1: the current release is never pruned."


class TestKeepFloor:
    """``plan_release_prune`` floors retention at 1, so ``--keep 0`` used to mean ``--keep 1``
    with nothing printed: an operator asking to drop all history silently kept the newest row
    and its image. The impossible ask is refused at the CLI instead."""

    @pytest.mark.parametrize("keep", [0, -5])
    def test_prune_refuses_keep_below_one(self, ship, keep):
        with pytest.raises(typer.Exit) as exc:
            ship.prune(keep_releases=keep)

        assert exc.value.exit_code == 1
        assert ship.errors == [KEEP_FLOOR_REFUSAL]
        ship.orchestrator.prune_releases.assert_not_called()

    def test_switch_refuses_keep_below_one(self, ship):
        with pytest.raises(typer.Exit) as exc:
            ship.switch(image="local/mybench:t9", keep=0)

        assert exc.value.exit_code == 1
        assert ship.errors == [KEEP_FLOOR_REFUSAL]
        ship.orchestrator.deploy.assert_not_called()

    def test_keep_one_is_still_accepted(self, ship):
        ship.orchestrator.prune_releases.return_value = {"entries": 0, "kept": 1, "backups": [], "images": []}

        ship.prune(keep_releases=1)

        ship.orchestrator.prune_releases.assert_called_once_with(keep=1, dry_run=True)


class TestPlanningIsSeparateFromApplying:
    """The structural guarantees the plan/apply split exists to provide.

    These are not per-flag behaviours; they are properties of the shape. Each one replaces a
    defect that came from deciding and acting in the same pass, measured on a live bench.
    """

    def test_one_invocation_recreates_a_container_once(self, world):
        """Three arms each called `compose.up(force_recreate=True)`, so a multi-flag run destroyed
        and recreated `frappe` three times -- counted from the docker event stream, not inferred.
        The plan accumulates a SET of services and applies it once."""
        world.run(environment=FMBenchEnvType.prod, developer_mode=EnableDisableOptionsEnum.enable)

        assert len(world.compose_up_calls) == 1
        assert world.compose_up_calls[0].kwargs == {
            "services": ["frappe"],
            "detach": True,
            "force_recreate": True,
        }

    def test_a_full_recreate_absorbs_the_runtime_restarts(self, world):
        """A restart-policy change recreates every container, so restarting the same processes
        again for the runtime change would bounce containers that came up seconds earlier."""
        world.run(restart_policy=RestartPolicyEnum.unless_stopped, python_version="3.12")

        world.bench.restart_web_containers_services.assert_not_called()
        world.bench.restart_workers_containers_services.assert_not_called()
        assert world.compose_up_calls[0].kwargs == {"detach": True, "force_recreate": True}

    def test_compose_is_rendered_once_for_several_flags(self, world):
        world.run(environment=FMBenchEnvType.prod, restart_policy=RestartPolicyEnum.unless_stopped)

        world.bench.generate_compose.assert_called_once()

    def test_the_config_is_saved_once_for_several_flags(self, world):
        world.run(
            environment=FMBenchEnvType.prod,
            restart_policy=RestartPolicyEnum.unless_stopped,
            developer_mode=EnableDisableOptionsEnum.enable,
        )

        assert world.saves == 1

    def test_the_config_is_saved_before_any_container_is_touched(self, world):
        """Ordering, not bookkeeping. A failure in the container half must leave the file AHEAD of
        the containers: fm's own regeneration paths push config onto containers, so "config ahead"
        self-heals, while "containers ahead" is what let a prod bench serve prod while every fm
        surface reported dev."""
        world.bench.docker_client.compose.up.side_effect = RuntimeError("daemon gone")

        with pytest.raises(RuntimeError):
            world.run(environment=FMBenchEnvType.prod)

        assert world.saves == 1

    def test_a_flagless_update_does_nothing_and_says_so(self, world):
        world.run()

        assert world.prints == ["mybench.localhost: nothing to do"]
        world.bench.generate_compose.assert_not_called()
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_an_environment_already_set_is_not_reapplied(self, world):
        """`-e prod` on a prod bench recreated the web container on every invocation, because
        only `--restart-policy` had an equality check."""
        world.config.environment_type = FMBenchEnvType.prod

        world.run(environment=FMBenchEnvType.prod)

        assert world.prints == ["mybench.localhost: nothing to do (environment is already 'prod')"]
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_developer_mode_already_set_is_not_reapplied(self, world):
        world.config.developer_mode = True

        world.run(developer_mode=EnableDisableOptionsEnum.enable)

        world.bench.set_common_bench_config.assert_not_called()
        assert world.saves == 0


class TestDryRun:
    """`--dry-run` prints the plan and changes nothing, per the flag vocabulary."""

    def test_it_reports_the_change_without_making_it(self, world):
        world.run(environment=FMBenchEnvType.prod, dry_run=True)

        assert "  environment  dev -> prod" in world.prints
        assert world.config.environment_type == FMBenchEnvType.dev
        world.bench.generate_compose.assert_not_called()
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_it_names_the_containers_it_would_touch(self, world):
        world.run(restart_policy=RestartPolicyEnum.unless_stopped, dry_run=True)

        assert any("recreate all bench services, workers and admin tools" in line for line in world.prints)
        assert world.compose_up_calls == []

    def test_it_prints_a_line_even_when_there_is_nothing_to_do(self, world):
        """`fm migrate --dry-run` prints NOTHING when nothing is pending, and its own docs have to
        warn scripts not to trust the exit code alone. One unconditional line is cheaper."""
        world.run(dry_run=True)

        assert world.prints == ["mybench.localhost: nothing to do"]

    def test_it_still_refuses_an_invalid_flag(self, world):
        """Planning is where refusals live, and --dry-run runs the real planner, not a copy."""
        with pytest.raises(typer.BadParameter):
            world.run(upload_limit="BOGUS", dry_run=True)

    def test_it_says_how_the_workers_will_be_treated(self, world):
        """Which it is -- waited for, or killed -- must be visible BEFORE committing to the run."""
        world.run(python_version="3.12", dry_run=True)

        assert "  workers: drain first (wait up to 300s for in-flight jobs, abort if still busy)" in world.prints
        world.bench.app_manager.setup_python_and_node_environments.assert_not_called()

    def test_no_drain_is_reported_as_an_interruption(self, world):
        world.run(python_version="3.12", drain=False, dry_run=True)

        assert "  workers: interrupt in-flight jobs (SIGUSR1, force-stop after 15s)" in world.prints


class TestStandaloneVenvRebuild:
    """`--recreate-python-env` with no version change is the explicit venv rebuild.

    It was previously reachable only as a side effect of re-passing the version the bench already
    had, which cost ~2 minutes and an undrained worker restart to find out. Now that an unchanged
    version is a no-op, the rebuild needs a name -- and this flag already meant "rebuild the venv".
    """

    def test_it_rebuilds_without_any_version_change(self, world):
        world.run(recreate_python_env=True)

        world.bench.app_manager.setup_python_and_node_environments.assert_called_once_with(
            use_run=True, recreate_python_env=True
        )
        assert world.config.python_version == "3.11"

    def test_it_reports_the_recorded_versions_it_will_rebuild_at(self, world):
        world.run(recreate_python_env=True, dry_run=True)

        assert any("rebuild the venv at the recorded python 3.11 / node 18" in line for line in world.prints)
        world.bench.app_manager.setup_python_and_node_environments.assert_not_called()

    def test_it_still_defaults_to_recreating_alongside_a_version_change(self, world):
        """Unset must keep meaning "yes" when --python moves the interpreter: a new interpreter
        needs a fresh venv."""
        world.run(python_version="3.12")

        world.bench.app_manager.setup_python_and_node_environments.assert_called_once_with(
            use_run=True, recreate_python_env=True
        )

    def test_no_recreate_alongside_a_version_change_keeps_the_venv(self, world):
        world.run(python_version="3.12", recreate_python_env=False)

        world.bench.app_manager.setup_python_and_node_environments.assert_called_once_with(
            use_run=True, recreate_python_env=False
        )

    def test_no_recreate_on_its_own_is_nothing_to_do(self, world):
        """"Do not rebuild the venv" with nothing else asked for is a request for no work."""
        world.run(recreate_python_env=False)

        world.bench.app_manager.setup_python_and_node_environments.assert_not_called()
        assert world.prints == ["mybench.localhost: nothing to do"]


class TestContainerFailureIsExplained:
    def test_a_failed_recreate_says_the_settings_are_already_recorded(self, world):
        """The config is saved first by design, so the user must be told a retry is safe and that
        nothing was lost -- otherwise a failed command looks like it left an unknown state."""
        world.bench.docker_client.compose.up.side_effect = RuntimeError("daemon gone")

        with pytest.raises(RuntimeError):
            world.run(environment=FMBenchEnvType.prod)

        assert any("already recorded in bench_config.toml" in w for w in world.warnings)
        assert any("fm restart" in w for w in world.warnings)


class TestWorkerDrainGate:
    """`fm update` drains RQ workers before disturbing them, like `fm restart` and `fm apps add`.

    It was the one worker-touching command without this. Two paths interrupt jobs: the runtime
    restart (SIGUSR1 then a force-stop) and a `--restart-policy` change, which RECREATES the
    workers project and so killed in-flight jobs with no warning at all.

    The gate runs before the first write, so a timeout aborts an update that changed nothing --
    a promise only the plan/apply split makes true.
    """

    def test_the_runtime_path_drains_first(self, world):
        world.run(python_version="3.12")

        world.orchestrator.drain_workers.assert_called_once_with()
        world.orchestrator.resume_workers.assert_called_once_with()

    def test_a_restart_policy_change_drains_too(self, world):
        """The path that used to kill jobs silently: recreating the workers project never gives
        RQ the SIGUSR1 courtesy at all."""
        world.run(restart_policy=RestartPolicyEnum.unless_stopped)

        world.orchestrator.drain_workers.assert_called_once_with()

    def test_a_plan_that_leaves_workers_alone_does_not_drain(self, world):
        world.run(environment=FMBenchEnvType.prod)

        world.orchestrator.drain_workers.assert_not_called()

    def test_a_timeout_aborts_before_anything_is_written(self, world):
        """"Nothing was changed" has to be literally true, and the workers must be resumed: the
        RQ suspend flag lives in redis and would outlive this command."""
        world.orchestrator.drain_workers.return_value = False

        with pytest.raises(typer.Exit) as exc:
            world.run(python_version="3.12")

        assert exc.value.exit_code == 1
        world.orchestrator.resume_workers.assert_called_once_with()
        assert world.saves == 0
        assert world.config.python_version == "3.11"
        world.bench.app_manager.setup_python_and_node_environments.assert_not_called()
        assert any("Nothing was changed" in e for e in world.errors)

    def test_no_drain_skips_the_gate_and_says_it_is_interrupting(self, world):
        world.run(python_version="3.12", drain=False)

        world.orchestrator.drain_workers.assert_not_called()
        world.orchestrator.resume_workers.assert_not_called()
        assert any("WITHOUT draining" in w for w in world.warnings)
        world.bench.app_manager.setup_python_and_node_environments.assert_called_once()

    def test_an_image_that_cannot_drain_warns_and_continues(self, world):
        """DrainUnavailable is not a timeout -- no drain_timeout can fix an image without fmx --
        so it must not abort the update."""
        world.orchestrator.drain_workers.side_effect = DrainUnavailable("no fmx in this image.")

        world.run(python_version="3.12")

        assert any("Continuing without a drain" in w for w in world.warnings)
        world.bench.app_manager.setup_python_and_node_environments.assert_called_once()
        world.orchestrator.resume_workers.assert_not_called()

    def test_workers_are_resumed_even_when_the_apply_fails(self, world):
        """Suspended workers outliving a failed command is a silent outage: nothing processes
        the queue until someone resumes them by hand."""
        world.bench.app_manager.setup_python_and_node_environments.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            world.run(python_version="3.12")

        world.orchestrator.resume_workers.assert_called_once_with()


class TestExternalRedis:
    """`[redis]` stops being a one-way door.

    `fm create --redis-cache/--redis-queue` wrote the table and nothing changed it afterwards, so
    moving a bench onto a managed redis (or back off one) meant hand-editing bench_config.toml --
    which was also the only path with NO validation, since create's scheme refusal never sees a
    hand edit. That is why the loader warns instead of raising on a bad scheme: raising on read
    would take `fm list` down for every bench on the host.
    """

    CACHE = "redis://r.example:6379/0"
    QUEUE = "redis://r.example:6379/1"

    def test_both_urls_are_recorded_and_pushed_to_the_bench(self, world):
        """The config save alone changes nothing that RUNS: every process reads its redis from
        common_site_config.json, so that file is what makes the endpoints take effect."""
        world.run(redis_cache=self.CACHE, redis_queue=self.QUEUE)

        assert world.config.redis.cache == self.CACHE
        assert world.config.redis.queue == self.QUEUE
        assert world.saves == 1
        pushed = world.bench.set_common_bench_config.call_args.args[0]
        assert pushed["redis_cache"] == self.CACHE
        assert pushed["redis_queue"] == self.QUEUE

    def test_one_side_alone_moves_only_that_side(self, world):
        """A SPLIT is the point, not a half-configured mistake: the queue is the stateful half
        worth paying a provider for, while the cache is throwaway and latency-sensitive. Frappe
        has always taken the two as separate config keys -- requiring both was fm's restriction."""
        world.run(redis_queue=self.QUEUE)

        assert world.config.redis.queue == self.QUEUE
        assert world.config.redis.cache is None
        pushed = world.bench.set_common_bench_config.call_args.args[0]
        assert pushed["redis_queue"] == self.QUEUE
        assert pushed["redis_cache"] == "redis://fm__mybench__redis-cache:6379"

    def test_moving_one_side_leaves_the_other_where_it_was(self, world):
        """Starting from a split, naming the cache must not drag the queue back."""
        world.config.redis = RedisConfig(queue=self.QUEUE)

        world.run(redis_cache=self.CACHE)

        assert world.config.redis.cache == self.CACHE
        assert world.config.redis.queue == self.QUEUE

    def test_a_side_can_be_reverted_on_its_own(self, world):
        world.config.redis = RedisConfig(cache=self.CACHE, queue=self.QUEUE)

        world.run(no_redis_queue=True)

        assert world.config.redis.cache == self.CACHE
        assert world.config.redis.queue is None

    def test_reverting_the_last_side_drops_the_table(self, world):
        """An empty `[redis]` table is not a shape: both-managed is spelled by having no table."""
        world.config.redis = RedisConfig(queue=self.QUEUE)

        world.run(no_redis_queue=True)

        assert world.config.redis is None

    def test_a_side_cannot_be_moved_and_reverted_at_once(self, world):
        with pytest.raises(typer.BadParameter) as exc:
            world.run(redis_queue=self.QUEUE, no_redis_queue=True)

        assert "opposite intents" in exc.value.message
        assert world.saves == 0

    def test_an_unsupported_scheme_is_refused(self, world):
        """The validation create had and the hand-edit path never got."""
        with pytest.raises(typer.BadParameter) as exc:
            world.run(redis_cache="http://r.example:6379/0", redis_queue=self.QUEUE)

        assert "--redis-cache" in exc.value.message
        assert world.saves == 0

    def test_a_shared_logical_database_is_refused(self, world):
        """A restore calls `frappe.cache.delete_keys("")`, a mass delete, so one shared index
        would destroy the queue along with the cache."""
        with pytest.raises(typer.BadParameter):
            world.run(redis_cache=self.CACHE, redis_queue=self.CACHE)

        assert world.saves == 0

    def test_no_redis_reverts_to_the_managed_containers(self, world):
        world.config.redis = RedisConfig(cache=self.CACHE, queue=self.QUEUE)

        world.run(no_redis=True)

        assert world.config.redis is None
        assert world.saves == 1

    def test_clearing_the_table_still_saves_the_config(self, world):
        """`--no-redis` sets the target to None, which an `is not None` scan reads as "not
        changing": the save would be skipped and the recreated containers would run on a config
        the file no longer described."""
        world.config.redis = RedisConfig(cache=self.CACHE, queue=self.QUEUE)

        world.run(no_redis=True)

        world.bench.save_bench_config.assert_called_once_with()

    def test_the_two_intents_cannot_be_combined(self, world):
        with pytest.raises(typer.BadParameter) as exc:
            world.run(no_redis=True, redis_cache=self.CACHE, redis_queue=self.QUEUE)

        assert "--no-redis already covers both sides" in exc.value.message
        assert world.saves == 0

    def test_the_same_endpoints_again_is_nothing_to_do(self, world):
        world.config.redis = RedisConfig(cache=self.CACHE, queue=self.QUEUE)

        world.run(redis_cache=self.CACHE, redis_queue=self.QUEUE)

        assert world.prints == [
            "mybench.localhost: nothing to do "
            f"(redis is already cache {self.CACHE}, queue {self.QUEUE})"
        ]
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_no_redis_on_a_managed_bench_is_nothing_to_do(self, world):
        world.run(no_redis=True)

        assert any("already fm's own per-bench containers" in line for line in world.prints)
        assert world.compose_up_calls == []

    def test_the_whole_bench_is_recreated(self, world):
        """The two per-bench redis CONTAINERS appear or disappear with this setting, and every
        process holds its connection from start-up, so a named subset would leave a queue nothing
        reads and a frappe still dialling the old endpoint."""
        world.run(redis_cache=self.CACHE, redis_queue=self.QUEUE)

        world.bench.generate_compose.assert_called_once()
        assert world.compose_up_calls[0].kwargs == {"detach": True, "force_recreate": True}

    def test_it_warns_that_queued_jobs_do_not_move(self, world):
        world.run(redis_cache=self.CACHE, redis_queue=self.QUEUE, dry_run=True)

        assert any("do NOT move with the endpoint" in w for w in world.warnings)
        assert world.saves == 0

    def test_going_external_removes_fms_own_redis_containers(self, world):
        """The `disabled` compose profile stops compose STARTING them; it does not stop ones
        already running, and an `up` ignores a service whose profile is inactive. Measured on a
        live bench: after a switch both fm redis containers were still up, serving a queue nothing
        read. Removed BY NAME, because `down --remove-orphans` would take the workers and
        admin-tools containers with them: fm's compose files share one directory, so one project."""
        world.run(redis_cache=self.CACHE, redis_queue=self.QUEUE)

        world.bench.docker_client.compose.rm.assert_called_once_with(
            services=["redis-cache", "redis-queue"], stop=True, force=True
        )

    def test_only_the_side_that_moved_out_is_removed(self, world):
        """A bench with an external queue and a local cache must KEEP its redis-cache container:
        removing both would take away the side fm still owns and serves."""
        world.run(redis_queue=self.QUEUE)

        world.bench.docker_client.compose.rm.assert_called_once_with(
            services=["redis-queue"], stop=True, force=True
        )

    def test_reverting_does_not_remove_them(self, world):
        """Going back the other way must LEAVE them: the profile is cleared, so the recreate is
        what starts them, and removing them first would be a pointless extra churn."""
        world.config.redis = RedisConfig(cache=self.CACHE, queue=self.QUEUE)

        world.run(no_redis=True)

        world.bench.docker_client.compose.rm.assert_not_called()

    def test_an_empty_queue_needs_no_maintenance_window(self, world):
        """A quiet bench switches straight through: there is nothing to lose, so no outage."""
        world.queue_depth = (0, 0)

        world.run(redis_queue=self.QUEUE)

        assert any("queue is empty, so no maintenance window" in line for line in world.prints)
        world.orchestrator.set_maintenance_mode.assert_not_called()

    def test_a_backlog_pauses_producers_and_waits(self, world):
        """The OPPOSITE of the drain gate, and both are needed. RQ's suspend stops workers PICKING
        UP work, so it freezes a backlog and could never empty one; emptying means stopping the
        producers instead (frappe's maintenance_mode makes is_scheduler_inactive true) and letting
        the workers eat. Queued jobs are the only redis data that cannot be regenerated, and they
        do not move with the endpoint."""
        world.queue_depth = [(41, 2), (12, 0), (0, 0)]

        world.run(redis_queue=self.QUEUE)

        assert world.orchestrator.set_maintenance_mode.call_args_list[0].args == (1,)
        assert world.orchestrator.set_maintenance_mode.call_args_list[-1].args == (0,)
        assert world.config.redis.queue == self.QUEUE

    def test_the_plan_says_a_window_is_coming_before_anything_changes(self, world):
        world.queue_depth = (41, 2)

        world.run(redis_queue=self.QUEUE, dry_run=True)

        assert any("41 pending and 2 in flight" in line for line in world.prints)
        assert any("maintenance page, HTTP 503" in line for line in world.prints)
        assert world.saves == 0
        world.orchestrator.set_maintenance_mode.assert_not_called()

    def test_a_backlog_that_never_drains_changes_nothing(self, world):
        """Producers are resumed on the way out, so the bench is left exactly as it was found."""
        world.queue_depth = (41, 2)
        world.config.workers = WorkersConfig(drain_timeout=0, drain_poll=0)

        with pytest.raises(typer.Exit) as exc:
            world.run(redis_queue=self.QUEUE)

        assert exc.value.exit_code == 1
        assert world.saves == 0
        assert world.compose_up_calls == []
        assert world.orchestrator.set_maintenance_mode.call_args_list[-1].args == (0,)
        assert any("--abandon-queued" in e for e in world.errors)

    def test_abandon_queued_switches_without_pausing_anything(self, world):
        world.queue_depth = (41, 2)

        world.run(redis_queue=self.QUEUE, abandon_queued=True)

        world.orchestrator.set_maintenance_mode.assert_not_called()
        assert world.config.redis.queue == self.QUEUE

    def test_an_uncountable_queue_warns_rather_than_claiming_it_is_empty(self, world):
        """"Could not tell" must never read as "nothing to lose": an unreachable endpoint or a
        provider that restricts the commands RQ uses lands here."""
        world.queue_depth = None

        world.run(redis_queue=self.QUEUE, dry_run=True)

        assert any("could not count what is queued" in w for w in world.warnings)

    def test_moving_only_the_cache_never_pauses_producers(self, world):
        """The cache is derived data that WANTS to be cold after a cutover; only the queue holds
        work that cannot be regenerated."""
        world.queue_depth = (41, 2)

        world.run(redis_cache=self.CACHE)

        world.orchestrator.set_maintenance_mode.assert_not_called()
        assert world.config.redis.cache == self.CACHE
