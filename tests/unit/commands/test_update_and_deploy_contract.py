"""
Characterization tests for the ``fm update`` and ``fm switch``/``fm prune`` decision tables.

``fm update`` mutates a LIVE bench: it toggles developer mode, flips the environment, rewrites the
restart policy, edits the upload limit, wires NewRelic, moves Python/Node and refreshes the external
database CA. Every one of those is guarded, and several of them re-render compose files and recreate
containers. The interesting content of that module is therefore not the plumbing but the DECISION
TABLE:

* which flag is refused on which runtime, and with exactly which message;
* which flag requires another one (``--newrelic`` needs a license key);
* which flag only writes config, which one re-renders compose, and which one restarts containers;
* whether the in-memory ``bench_config`` mutation is actually persisted
  (``bench_config_save``/``save_bench_config`` bookkeeping), including the paths where it is not.

Apps (``fm apps add``), admin tools (``fm tools enable``/``disable``) and alias domains
(``fm domain add``/``remove``) used to live here as flags on this command; they now have their own
contract files (``test_apps_contract.py``, ``test_tools_contract.py``, ``test_domain_contract.py``)
and are not re-pinned in this one. The runtime conversion (``--runtime mount``/``--runtime image``)
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
from unittest.mock import MagicMock, patch, call

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
    MonitoringConfig,
    NewRelicConfig,
    RestartPolicyEnum,
)
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError

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
        cfg.monitoring = None
        # The command reads monitoring through the helper and writes it back through the
        # attribute, so the double has to keep the two consistent.
        cfg.get_newrelic_config.side_effect = lambda: cfg.monitoring.newrelic if cfg.monitoring else None
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

        p = stack.enter_context
        p(patch("frappe_manager.commands.update.Bench", bench_cls))
        p(patch("frappe_manager.commands.update.spinner", _null_spinner))
        p(patch("frappe_manager.commands.update.AppConfig", self.app_config_cls))
        p(patch("frappe_manager.commands.update.extract_python_version_requirement", self.extract_python_req))
        p(patch("frappe_manager.commands.update.extract_node_version_requirement", self.extract_node_req))
        p(patch("frappe_manager.commands.update.validate_python_version_compatibility", self.python_compat))
        p(patch("frappe_manager.commands.update.validate_node_version_compatibility", self.node_compat))
        p(patch("frappe_manager.commands.update.parse_python_version_for_runtime", self.parse_python))
        p(patch("frappe_manager.commands.update.parse_node_version_for_runtime", self.parse_node))
        p(patch("frappe_manager.commands.update.check_bench_migration_required", self.check_migration))
        p(patch("frappe_manager.site_manager.modules.db_tls.install_site_ca", self.install_site_ca))

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
    "'fm update' on an image bench still changes environment, restart policy, NewRelic and the database CA."
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

        assert world.prints == [
            "Installed CA at /workspace/config/tls/db-ca.pem and rebuilt the bench ca-bundle.pem",
            "Running containers read the new CA on their next database connection; no restart needed.",
        ]
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
        world.run(restart_policy=RestartPolicyEnum.always)

        assert world.prints == ["Restart policy is already set to 'always'"]
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

    def test_no_restart_on_production_warns_twice(self, world):
        world.config.environment_type = FMBenchEnvType.prod

        world.run(restart_policy=RestartPolicyEnum.no)

        assert world.warnings == [
            "Setting restart policy to 'no' on production bench",
            "Containers will not auto-recover from failures or system reboots",
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
        world.run(upload_limit="500M")

        world.bench.update_upload_limit.assert_called_once_with("500M")
        assert world.saves == 0
        assert world.compose_up_calls == []


class TestNewRelic:
    def test_enabling_without_any_license_key_is_rejected(self, world):
        with pytest.raises(typer.BadParameter) as exc:
            world.run(newrelic=True)

        assert exc.value.message == "--newrelic-license-key is required when enabling NewRelic."
        world.bench.supervisor.setup_newrelic.assert_not_called()

    def test_a_missing_license_key_is_rejected_before_any_flag_is_applied(self, world):
        """The check used to sit in the middle of the decision table: ``-e prod`` had already rewritten
        the compose file and force-recreated the frappe container by the time the usage error aborted the
        pending save, so FRAPPE_ENV=prod ran in the container while bench_config.toml still said dev."""
        with pytest.raises(typer.BadParameter):
            world.run(environment=FMBenchEnvType.prod, newrelic=True)

        assert world.config.environment_type == FMBenchEnvType.dev
        world.bench.generate_compose.assert_not_called()
        assert world.compose_up_calls == []
        assert world.saves == 0

    def test_enabling_accepts_a_key_already_stored_in_the_bench_config(self, world):
        world.config.monitoring = MonitoringConfig(newrelic=NewRelicConfig(license_key="stored-key"))

        world.run(newrelic=True)

        assert world.config.monitoring.newrelic.enabled is True
        world.bench.supervisor.setup_newrelic.assert_called_once_with(world.bench_path)

    def test_disabling_needs_no_license_key(self, world):
        world.run(newrelic=False)

        assert world.config.monitoring.newrelic.enabled is False
        world.bench.supervisor.setup_newrelic.assert_called_once_with(world.bench_path)

    def test_a_license_key_alone_enters_the_block_and_restarts_frappe(self, world):
        world.run(newrelic_license_key="ingest-key")

        assert world.config.monitoring.newrelic.license_key == "ingest-key"
        world.bench.generate_compose.assert_called_once()
        world.bench.supervisor.setup_newrelic.assert_called_once_with(world.bench_path)
        assert world.compose_up_calls[0].kwargs == {"services": ["frappe"], "detach": True, "force_recreate": True}
        assert "NewRelic configuration updated" in world.prints

    def test_the_block_saves_once_and_clears_pending_saves(self, world, tmp_path):
        """NewRelic folds into the one terminal save at the bottom like every other flag now, so a
        --db-ca in the same run still saves exactly once."""
        ca = tmp_path / "ca.pem"
        ca.write_text("ca")

        world.run(db_ca=ca, newrelic_license_key="ingest-key")

        assert world.saves == 1
        assert world.database_config.ca == str(ca.absolute())

    def test_neither_option_skips_the_block_entirely(self, world):
        world.run(upload_limit="100M")

        world.bench.supervisor.setup_newrelic.assert_not_called()


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

        assert world.warnings == [
            " Python 3.9 is incompatible with Frappe requirement",
            " Consider using --python 3.12 instead",
            "Restarting workers WITHOUT draining: in-flight jobs are interrupted (SIGUSR1, force-stop after 15s)",
        ]
        assert world.config.python_version == "3.9"
        world.bench.app_manager.setup_python_and_node_environments.assert_called_once()

    def test_missing_current_version_is_reported_as_not_set(self, world):
        world.bench.app_manager.get_current_runtime_versions.return_value = {}

        world.run(python_version="3.12")

        world.bench.app_manager.get_current_runtime_versions.assert_called_once_with(use_run=True)
        assert "Python: not set -> 3.12" in world.prints

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
            " Node 16 is incompatible with Frappe requirement",
            " Consider using --node 20 instead",
            "Restarting workers WITHOUT draining: in-flight jobs are interrupted (SIGUSR1, force-stop after 15s)",
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

        assert world.warnings == [
            "No apps.txt found, skipping app reinstallation",
            "Restarting workers WITHOUT draining: in-flight jobs are interrupted (SIGUSR1, force-stop after 15s)",
        ]
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
