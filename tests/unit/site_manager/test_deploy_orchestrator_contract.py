"""Characterization contract for the image deploy / switch / rollback pipeline.

``DeployOrchestrator`` is the highest-consequence module in the product: a bug
here leaves a customer's bench half-migrated, dark behind a maintenance page, or
running on the wrong image tag. These tests pin the DECISIONS and the PHASE
ORDER, not the plumbing:

* what ``__init__`` binds and what ``_require_image_mode`` re-binds / refuses,
* the maintenance-mode window -- when it opens, which phases it spans, and every
  path that closes it again,
* the ``migrate = true | false`` resolution and the runtime override that beats it,
* the backup decision (``backup_db`` true/false/``'auto'``) and the fact that a
  ``--restore-db`` dump counts as a schema step exactly like a migrate,
* where ``drain_workers`` sits and that a ``False`` return is an ABORT GATE --
  workers resumed, nothing backed up, migrated or swapped,
* the pre-swap abort handler (compose restored, maintenance dropped),
* the migrate-failure path (no swap, failure notifications, conditional
  ``rollback_db`` restore) and the health-gate rollback path,
* every hook invocation point and its host/container ordering + env.

They are characterization tests: they describe TODAY's behaviour so a later
refactor is provably behaviour-preserving. Where the current behaviour looks
surprising it is pinned as-is, not fixed.
"""

import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.site_manager.bench_config import (
    BenchRuntime,
    DeployState,
    DeployStateEntry,
    SwitchConfig,
    SwitchHooks,
    SwitchHookScripts,
    WorkersConfig,
)
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.deploy_orchestrator import (
    BENCH_BIN,
    DeployError,
    DeployOrchestrator,
    DrainUnavailable,
    RestoreNotConfirmed,
)

SITE = "shop.localhost"
#: The bench's SECOND site. Named so it sorts BEFORE the primary, deliberately: the multi-site
#: tests pin `site_names` order (primary first), and a loop that sorted, set-ified or reversed
#: its sites would otherwise agree with the expected order by accident and pass anyway.
SITE2 = "annex.localhost"
NEW_TAG = "reg.example/shop:v2"
OLD_TAG = "reg.example/shop:v1"


# --------------------------------------------------------------------- fakes


class FakeConfig:
    """Duck-typed stand-in for BenchConfig: only what the orchestrator reads."""

    def __init__(self, root_path, switch=None, workers=None, deploy_state=None, site_names=None):
        self.runtime = BenchRuntime.image
        self.image = NEW_TAG
        self.switch = switch if switch is not None else SwitchConfig()
        self.workers = workers
        self.root_path = str(root_path)
        self.deploy_state = deploy_state
        # Every site the bench serves, primary first: the list every schema-grade step of the
        # pipeline walks. One entry unless a test asks for more, so the single-site cases below
        # keep pinning the single call they always pinned.
        self.site_names = list(site_names) if site_names else [SITE]
        self.db_name = "_shopdb"
        self.apps_list = []
        self.seed_image = None
        self.base_image = None
        self.registry = None
        self.database = {}
        self.export_to_toml = MagicMock()

    def get_database_config(self, site):
        return self.database.get(site)


def make_bench(tmp_path, config):
    bench_path = tmp_path / "bench"
    (bench_path / "workspace" / "frappe-bench" / "logs").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        # bench, site and domain are one string today, and this stand-in must carry all three
        # because the orchestrator correctly asks for the site where it means the site.
        name=SITE,
        site_name=SITE,
        primary_domain=SITE,
        domains=[SITE],
        path=bench_path,
        bench_config=config,
        docker_client=MagicMock(),
        compose_file_manager=MagicMock(),
        docker_ops=MagicMock(),
        workers=MagicMock(),
        set_common_bench_config=MagicMock(),
        set_bench_site_config=MagicMock(),
        unmanaged_site_dirs=MagicMock(return_value=[]),
    )


def make_orch(tmp_path, switch=None, workers=None, deploy_state=None, site_names=None):
    config = FakeConfig(
        tmp_path,
        switch=switch,
        workers=workers,
        deploy_state=deploy_state,
        site_names=site_names,
    )
    bench = make_bench(tmp_path, config)
    return DeployOrchestrator(bench, output_handler=MagicMock())


def docker_error(msg="boom", stdout=None, stderr=None, exit_code=1):
    return DockerException(
        ["docker", "compose", "run"], SubprocessOutput(stdout or [msg], stderr or [], [msg], exit_code)
    )


# --------------------------------------------------------------- deploy rig


#: Everything ``deploy()`` delegates to. Replaced by spies attached to one
#: parent mock so ``manager.mock_calls`` is a single ordered transcript.
SPIED = (
    "_fetch_image",
    "_snapshot_compose",
    "_restore_compose",
    "_pin_workers",
    "_set_maintenance",
    "drain_workers",
    "resume_workers",
    "_backup_all",
    "_restore_db",
    "_migrate",
    "_notify_after_migrate",
    "_run_host_hook",
    "_run_container_hook",
    "_rolling_swap",
    "_up_workers",
    "_health_check",
    "_ensure_nginx",
    "_install_new_apps",
    "_apply_config_merges",
    "_exec_frappe",
    "_record",
    "rollback",
    "prune_releases",
)

DEFAULT_RESULTS = {
    "_snapshot_compose": {"return_value": {"snap": b"x"}},
    "drain_workers": {"return_value": True},
    "_health_check": {"return_value": True},
}


class Rig:
    """A DeployOrchestrator whose collaborators are ordered spies."""

    def __init__(self, orch, manager, backups):
        self.orch = orch
        self.manager = manager
        #: What the spied ``_backup_all`` returns: one dump per site, keyed by site.
        self.backups = backups

    @property
    def order(self):
        """Phase transcript, minus the ``_frappe_running`` gate probes."""
        return [name for name, _a, _k in self.manager.mock_calls if name != "_frappe_running"]

    def calls(self, name):
        return [(a, k) for n, a, k in self.manager.mock_calls if n == name]

    def hook_phases(self):
        return [
            (name, a[1], a[0])
            for name, a, _k in self.manager.mock_calls
            if name in ("_run_host_hook", "_run_container_hook")
        ]


@pytest.fixture
def rig(tmp_path):
    """Factory: ``rig(switch=..., running=True, ...)`` -> :class:`Rig`."""

    def _make(switch=None, workers=None, deploy_state=None, running=True, site_names=None, **overrides):
        orch = make_orch(
            tmp_path,
            switch=switch,
            workers=workers,
            deploy_state=deploy_state,
            site_names=site_names,
        )
        manager = MagicMock()
        backups = {site: tmp_path / "backups" / f"db-{site}.sql" for site in orch.sites}

        results = dict(DEFAULT_RESULTS)
        results["_backup_all"] = {"return_value": backups}
        for key, value in overrides.items():
            results[key] = value if isinstance(value, dict) else {"return_value": value}

        for name in SPIED:
            spy = MagicMock(**results.get(name, {}))
            manager.attach_mock(spy, name)
            setattr(orch, name, spy)

        running_spy = MagicMock(return_value=running)
        manager.attach_mock(running_spy, "_frappe_running")
        orch._frappe_running = running_spy

        for owner, attr, label in (
            (orch.docker, "run", "preflight_run"),
            (orch.docker.compose, "up", "compose_up"),
            (orch.docker_ops, "render_image_compose", "render_image_compose"),
        ):
            spy = MagicMock(**results.get(label, {}))
            manager.attach_mock(spy, label)
            setattr(owner, attr, spy)

        return Rig(orch, manager, backups)

    return _make


# ============================================================== construction


class TestBinding:
    """``switch_config`` / ``workers_config`` bind at ``__init__``."""

    def test_init_binds_switch_and_workers_from_config(self, tmp_path):
        switch = SwitchConfig(migrate=False, keep_releases=3)
        workers = WorkersConfig(drain=False, drain_timeout=42)
        orch = make_orch(tmp_path, switch=switch, workers=workers)
        assert orch.switch_config is switch
        assert orch.workers_config is workers

    def test_init_substitutes_default_workers_config_when_absent(self, tmp_path):
        orch = make_orch(tmp_path, workers=None)
        assert isinstance(orch.workers_config, WorkersConfig)
        assert orch.workers_config.drain is True
        assert orch.workers_config.drain_timeout == 300

    def test_init_leaves_migrate_state_unset(self, tmp_path):
        orch = make_orch(tmp_path)
        assert orch._migrate_status is None
        assert orch._migrate_log_host is None
        assert orch._migrate_log_container is None

    def test_require_image_mode_refuses_mount_runtime(self, tmp_path):
        orch = make_orch(tmp_path)
        orch.config.runtime = BenchRuntime.mount
        with pytest.raises(DeployError, match="not in image runtime"):
            orch._require_image_mode()

    def test_require_image_mode_refuses_missing_image(self, tmp_path):
        orch = make_orch(tmp_path)
        orch.config.image = None
        with pytest.raises(DeployError, match="No image configured"):
            orch._require_image_mode()

    def test_require_image_mode_rebinds_configs_replaced_after_init(self, tmp_path):
        """A config reloaded between construction and deploy must win."""
        orch = make_orch(tmp_path)
        fresh_switch = SwitchConfig(migrate=False)
        fresh_workers = WorkersConfig(drain=False)
        orch.config.switch = fresh_switch
        orch.config.workers = fresh_workers
        orch._require_image_mode()
        assert orch.switch_config is fresh_switch
        assert orch.workers_config is fresh_workers

    def test_require_image_mode_defaults_configs_that_are_none(self, tmp_path):
        orch = make_orch(tmp_path)
        orch.config.switch = None
        orch.config.workers = None
        orch._require_image_mode()
        assert orch.switch_config == SwitchConfig()
        assert orch.workers_config == WorkersConfig()


class TestSwitchHookLookup:
    def test_no_hooks_configured_yields_none(self, tmp_path):
        orch = make_orch(tmp_path, switch=SwitchConfig(hooks=None))
        assert orch._switch_hook("before_restart") is None
        assert orch._switch_hook("before_restart", host=True) is None

    def test_container_and_host_hooks_are_separate_slots(self, tmp_path):
        hooks = SwitchHooks(
            before_restart="echo container",
            host=SwitchHookScripts(before_restart="echo host"),
        )
        orch = make_orch(tmp_path, switch=SwitchConfig(hooks=hooks))
        assert orch._switch_hook("before_restart") == "echo container"
        assert orch._switch_hook("before_restart", host=True) == "echo host"

    def test_host_slot_absent_yields_none_not_the_container_value(self, tmp_path):
        hooks = SwitchHooks(before_restart="echo container", host=None)
        orch = make_orch(tmp_path, switch=SwitchConfig(hooks=hooks))
        assert orch._switch_hook("before_restart", host=True) is None


# ================================================================ the drain


class TestDrainWorkers:
    """The drain is an abort gate: True == safe to proceed."""

    def _drainable(self, tmp_path, **kw):
        orch = make_orch(tmp_path, workers=WorkersConfig(**kw))
        orch._frappe_running = MagicMock(return_value=True)
        orch._exec_frappe = MagicMock()
        return orch

    def test_drain_disabled_is_safe_without_touching_the_container(self, tmp_path):
        orch = self._drainable(tmp_path, drain=False)
        assert orch.drain_workers() is True
        orch._exec_frappe.assert_not_called()

    def test_frappe_not_running_is_safe_without_touching_the_container(self, tmp_path):
        orch = self._drainable(tmp_path)
        orch._frappe_running = MagicMock(return_value=False)
        assert orch.drain_workers() is True
        orch._exec_frappe.assert_not_called()

    def test_drain_suspends_then_waits_with_the_configured_budget(self, tmp_path):
        orch = self._drainable(
            tmp_path,
            drain_timeout=120,
            drain_poll=7,
            skip_stale=False,
            stale_timeout=9,
        )
        assert orch.drain_workers() is True
        (command,), _ = orch._exec_frappe.call_args
        assert "control_rq_workers(ActionEnum.suspend)" in command
        assert "wait_for_rq_workers_suspended(120, 7, skip_stale=False, stale_timeout=9)" in command
        # exit 3 is the timeout signal the wrapper turns into a False return.
        assert "else 3" in command

    def test_the_wait_giving_up_with_exit_3_is_the_timeout_and_returns_false(self, tmp_path):
        orch = self._drainable(tmp_path)
        orch._exec_frappe = MagicMock(side_effect=docker_error("exit 3", exit_code=3))
        assert orch.drain_workers() is False

    def test_an_exec_that_never_ran_is_not_reported_as_a_timeout(self, tmp_path):
        """Was `return False` for ANY exception, which `fm restart` renders as "drain
        timed out after 300s" -- on an image predating fmx the exec fails in
        milliseconds and no drain_timeout can ever make it succeed."""
        orch = self._drainable(tmp_path)
        orch._exec_frappe = MagicMock(
            side_effect=docker_error(
                'OCI runtime exec failed: exec: "/opt/uv-tools/fmx/bin/python": stat: no such file or directory',
                exit_code=127,
            )
        )
        with pytest.raises(DrainUnavailable) as exc:
            orch.drain_workers()
        assert "fmx" in str(exc.value)
        assert "image" in str(exc.value)
        assert "no such file or directory" in str(exc.value)
        assert "timed out" not in str(exc.value)

    def test_resume_is_a_noop_when_drain_is_disabled(self, tmp_path):
        orch = self._drainable(tmp_path, drain=False)
        orch.resume_workers()
        orch._exec_frappe.assert_not_called()

    def test_resume_issues_the_resume_action(self, tmp_path):
        orch = self._drainable(tmp_path)
        orch.resume_workers()
        (command,), _ = orch._exec_frappe.call_args
        assert "control_rq_workers(ActionEnum.resume)" in command

    def test_resume_failure_warns_but_never_raises(self, tmp_path):
        orch = self._drainable(tmp_path)
        orch._exec_frappe = MagicMock(side_effect=docker_error("no container"))
        orch.resume_workers()  # must not raise
        assert orch.output.warning.called


# ========================================================= deploy: ordering


class TestDeployPhaseOrder:
    def test_default_migrate_deploy_runs_the_documented_phase_order(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG)
        assert r.order == [
            "_fetch_image",
            "preflight_run",
            "_snapshot_compose",
            "render_image_compose",
            "_pin_workers",
            "_set_maintenance",  # ON
            "drain_workers",
            "_backup_all",
            "_run_host_hook",  # host_before_migrate
            "_run_container_hook",  # before_migrate
            "_migrate",
            "_run_container_hook",  # after_migrate
            "_run_host_hook",  # host_after_migrate
            "_run_host_hook",  # host_before_restart
            "_run_container_hook",  # before_restart
            "_rolling_swap",
            "_health_check",
            "resume_workers",
            "_install_new_apps",
            "_apply_config_merges",
            "_exec_frappe",  # clear-cache
            "_run_container_hook",  # after_restart
            "_run_host_hook",  # host_after_restart
            "_set_maintenance",  # OFF
            "_record",
        ]

    def test_preflight_boot_check_precedes_every_change(self, rig):
        r = rig(preflight_run={"side_effect": docker_error("no such image")})
        with pytest.raises(DeployError, match="Pre-flight boot check failed"):
            r.orch.deploy(NEW_TAG)
        assert r.order == ["_fetch_image", "preflight_run"]
        # Nothing was snapshotted, so nothing had to be restored.
        r.orch._restore_compose.assert_not_called()

    def test_fetch_failure_aborts_before_the_preflight(self, rig):
        r = rig(_fetch_image={"side_effect": DeployError("pull refused")})
        with pytest.raises(DeployError, match="pull refused"):
            r.orch.deploy(NEW_TAG)
        assert r.order == ["_fetch_image"]

    def test_compose_is_repinned_to_the_new_tag_before_the_migrate_decision(self, rig):
        r = rig(switch=SwitchConfig(migrate=True))
        r.orch.deploy(NEW_TAG)
        r.orch.docker_ops.render_image_compose.assert_called_once_with(NEW_TAG)
        r.orch._pin_workers.assert_any_call(NEW_TAG)
        assert r.order.index("render_image_compose") < r.order.index("_migrate")

    def test_backup_is_taken_at_the_quiesced_point(self, rig):
        """Maintenance on, workers drained, and only THEN the dump."""
        r = rig()
        r.orch.deploy(NEW_TAG)
        order = r.order
        assert order.index("_set_maintenance") < order.index("drain_workers") < order.index("_backup_all")
        assert order.index("_backup_all") < order.index("_migrate")

    def test_backup_directory_is_a_timestamped_deploy_dir_under_backups(self, rig, tmp_path):
        r = rig()
        r.orch.deploy(NEW_TAG)
        (backup_dir,), _ = r.orch._backup_all.call_args
        assert backup_dir.parent == tmp_path / "bench" / "backups"
        assert backup_dir.name.startswith("deploy-")


# =============================================== deploy: sites fm does not own


class TestUnmanagedSites:
    """Site directories ``[sites]`` does not record: named up front, never migrated.

    Same rule ``fm delete`` follows, because a migration fm broke on a schema it disclaimed
    ownership of would be damage to the exact thing it promised not to touch. The warning is
    louder here than at delete, though: an unmigrated site keeps serving, now against new code,
    so it is the operator's to migrate by hand.
    """

    def test_every_unmanaged_dir_is_warned_about_once(self, rig):
        r = rig()
        r.orch.bench.unmanaged_site_dirs = MagicMock(return_value=["legacy.localhost", "old.localhost"])
        r.orch.deploy(NEW_TAG)
        warned = [str(c.args) for c in r.orch.output.warning.call_args_list if "NOT migrate" in str(c.args)]
        assert len(warned) == 2
        assert "sites/legacy.localhost/" in warned[0]
        assert "sites/old.localhost/" in warned[1]
        assert "bench --site old.localhost migrate" in warned[1]

    def test_an_unmanaged_dir_is_left_out_of_the_walk_and_stops_nothing(self, rig):
        """``self.sites`` is the recorded list, and it is the list every schema-grade step walks,
        so the unmanaged directory is untouched rather than refused."""
        r = rig()
        r.orch.bench.unmanaged_site_dirs = MagicMock(return_value=["legacy.localhost"])
        r.orch.deploy(NEW_TAG)
        assert r.orch.sites == [SITE]
        r.orch._record.assert_called_once()

    def test_the_warning_lands_before_the_image_is_even_fetched(self, rig):
        """Before it starts, not after it finished: the operator has to be able to stop it."""
        r = rig(_fetch_image={"side_effect": DeployError("pull refused")})
        r.orch.bench.unmanaged_site_dirs = MagicMock(return_value=["legacy.localhost"])
        with pytest.raises(DeployError, match="pull refused"):
            r.orch.deploy(NEW_TAG)
        assert any("NOT migrate" in str(c.args) for c in r.orch.output.warning.call_args_list)

    def test_a_bench_with_no_unmanaged_dirs_says_nothing(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG)
        assert not any("NOT migrate" in str(c.args) for c in r.orch.output.warning.call_args_list)


# =================================================== deploy: migrate decision


class TestMigrateDecision:
    def test_migrate_true_migrates(self, rig):
        r = rig(switch=SwitchConfig(migrate=True))
        r.orch.deploy(NEW_TAG)
        r.orch._migrate.assert_called_once_with(NEW_TAG)

    def test_migrate_false_skips_migrate_and_records_skipped(self, rig):
        r = rig(switch=SwitchConfig(migrate=False))
        r.orch.deploy(NEW_TAG)
        r.orch._migrate.assert_not_called()
        assert r.orch._record.call_args.args[1] == "skipped"

    def test_runtime_override_false_beats_config_true(self, rig):
        """Rollbacks pass migrate_override=False: old code must never migrate."""
        r = rig(switch=SwitchConfig(migrate=True))
        r.orch.deploy(NEW_TAG, migrate_override=False)
        r.orch._migrate.assert_not_called()

    def test_runtime_override_true_beats_config_false(self, rig):
        r = rig(switch=SwitchConfig(migrate=False))
        r.orch.deploy(NEW_TAG, migrate_override=True)
        r.orch._migrate.assert_called_once_with(NEW_TAG)


# ==================================================== deploy: maintenance mode


class TestMaintenanceWindow:
    def test_window_opens_before_the_drain_and_closes_after_the_post_hooks(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG)
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]
        order = r.order
        on, off = (i for i, n in enumerate(order) if n == "_set_maintenance")
        assert on < order.index("drain_workers")
        assert on < order.index("_migrate")
        assert order.index("_rolling_swap") < off
        assert order.index("_health_check") < off
        assert max(i for i, n in enumerate(order) if n == "_run_host_hook") < off
        assert off < order.index("_record")

    def test_no_schema_step_means_no_maintenance_page_at_all(self, rig):
        r = rig(switch=SwitchConfig(migrate=False))
        r.orch.deploy(NEW_TAG)
        r.orch._set_maintenance.assert_not_called()

    def test_maintenance_mode_false_migrates_without_the_page(self, rig):
        r = rig(switch=SwitchConfig(migrate=True, maintenance_mode=False))
        r.orch.deploy(NEW_TAG)
        r.orch._set_maintenance.assert_not_called()
        r.orch._migrate.assert_called_once()

    def test_maintenance_needs_a_running_container_to_switch_on(self, rig):
        """No web to 503: the page cannot be set, but it is still 'a maintenance
        deploy' -- the OFF write at the end is issued unconditionally."""
        r = rig(running=False)
        r.orch.deploy(NEW_TAG)
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [0]

    def test_a_restore_dump_opens_the_window_even_without_a_migrate(self, rig, tmp_path):
        dump = tmp_path / "old.sql"
        r = rig(switch=SwitchConfig(migrate=False))
        r.orch.deploy(NEW_TAG, restore_db_dumps={SITE: dump})
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]
        r.orch._restore_db.assert_called_once_with(SITE, dump, requested=True, confirmed=False)

    def test_empty_maintenance_phases_skips_the_maintenance_window(self, rig):
        """``maintenance_mode_phases = []`` is the operator asserting the migration is
        backward-compatible, and the field's own description promises "no page". It used to
        buy nothing but rolling eligibility (`rolling_eligible` is the only other reader):
        the 503 still went up for the whole migrate + swap, so the operator who cleared
        phases to get a page-less additive migrate took the downtime anyway."""
        r = rig(switch=SwitchConfig(migrate=True, maintenance_mode=True, maintenance_mode_phases=[]))
        r.orch.deploy(NEW_TAG)
        assert r.calls("_set_maintenance") == []
        r.orch._migrate.assert_called_once_with(NEW_TAG)

    def test_a_populated_phases_list_still_opens_the_window_for_a_restore(self, rig, tmp_path):
        """The list gates the window's EXISTENCE, not which steps it spans: 'restore' is not a
        phase name, and a restore under the default ``["migrate"]`` still gets the page."""
        r = rig(switch=SwitchConfig(migrate=False, maintenance_mode_phases=["migrate"]))
        r.orch.deploy(NEW_TAG, restore_db_dumps={SITE: tmp_path / "old.sql"})
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]


# ======================================================== deploy: backup rule


class TestBackupDecision:
    def test_backup_db_true_dumps_even_without_a_schema_step(self, rig):
        r = rig(switch=SwitchConfig(migrate=False, backup_db=True))
        r.orch.deploy(NEW_TAG)
        r.orch._backup_all.assert_called_once()

    def test_backup_db_false_never_dumps(self, rig):
        r = rig(switch=SwitchConfig(migrate=True, backup_db=False))
        r.orch.deploy(NEW_TAG)
        r.orch._backup_all.assert_not_called()
        assert r.orch._record.call_args.kwargs["backups"] == {}

    def test_backup_db_auto_dumps_for_a_migrate(self, rig):
        r = rig(switch=SwitchConfig(migrate=True, backup_db="auto"))
        r.orch.deploy(NEW_TAG)
        r.orch._backup_all.assert_called_once()

    def test_backup_db_auto_skips_a_code_only_deploy_and_says_so(self, rig):
        r = rig(switch=SwitchConfig(migrate=False, backup_db="auto"))
        r.orch.deploy(NEW_TAG)
        r.orch._backup_all.assert_not_called()
        printed = " ".join(str(c.args) for c in r.orch.output.print.call_args_list)
        assert "backup_db=auto: no schema change" in printed

    def test_backup_db_auto_dumps_for_a_restore_only_deploy(self, rig, tmp_path):
        r = rig(switch=SwitchConfig(migrate=False, backup_db="auto"))
        r.orch.deploy(NEW_TAG, restore_db_dumps={SITE: tmp_path / "old.sql"})
        r.orch._backup_all.assert_called_once()

    def test_backup_db_false_is_silent_not_the_auto_message(self, rig):
        r = rig(switch=SwitchConfig(migrate=False, backup_db=False))
        r.orch.deploy(NEW_TAG)
        printed = " ".join(str(c.args) for c in r.orch.output.print.call_args_list)
        assert "backup_db=auto" not in printed

    def test_the_insurance_dump_precedes_the_requested_restore(self, rig, tmp_path):
        """`--restore-db` still gets a dump of the CURRENT state first."""
        r = rig(switch=SwitchConfig(migrate=False, backup_db="auto"))
        r.orch.deploy(NEW_TAG, restore_db_dumps={SITE: tmp_path / "old.sql"})
        assert r.order.index("_backup_all") < r.order.index("_restore_db")

    def test_no_restore_dump_means_no_restore_call(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG)
        r.orch._restore_db.assert_not_called()


# ==================================================== deploy: the drain gate


class TestDrainAbortGate:
    def test_drain_timeout_aborts_the_deploy_and_resumes_the_workers(self, rig):
        r = rig(workers=WorkersConfig(drain_timeout=90), drain_workers=False)
        with pytest.raises(DeployError) as exc:
            r.orch.deploy(NEW_TAG)
        assert "Drain timed out after 90s" in str(exc.value)
        r.orch.resume_workers.assert_called_once()

    def test_drain_timeout_refuses_backup_migrate_and_swap(self, rig):
        r = rig(drain_workers=False)
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._backup_all.assert_not_called()
        r.orch._migrate.assert_not_called()
        r.orch._rolling_swap.assert_not_called()
        r.orch.docker.compose.up.assert_not_called()
        r.orch._record.assert_not_called()

    def test_drain_timeout_unwinds_the_compose_repin_and_the_page(self, rig):
        r = rig(drain_workers=False)
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_compose.assert_called_once_with({"snap": b"x"})
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]


# ================================================ deploy: pre-swap abort path


class TestPreSwapAbort:
    def test_a_failing_pre_restart_hook_restores_compose_and_drops_the_page(self, rig):
        r = rig(_run_container_hook={"side_effect": [None, None, DeployError("hook exploded")]})
        with pytest.raises(DeployError, match="hook exploded"):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_compose.assert_called_once_with({"snap": b"x"})
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]
        r.orch._rolling_swap.assert_not_called()
        r.orch._record.assert_not_called()

    def test_abort_skips_the_maintenance_reset_when_nothing_is_running(self, rig):
        r = rig(running=False, _run_container_hook={"side_effect": DeployError("hook exploded")})
        with pytest.raises(DeployError, match="hook exploded"):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_compose.assert_called_once()
        r.orch._set_maintenance.assert_not_called()

    def test_a_pre_swap_abort_always_un_suspends_the_workers(self, rig):
        """A drained deploy that aborts must not leave RQ suspended.

        `drain_workers` suspends RQ through a PERSISTENT Redis key, so an abort path that skips the
        resume leaves the bench processing no background jobs at all -- no scheduled jobs, no
        emails, no backups -- while the site itself looks healthy and nothing on screen explains it.
        The site being back up is not enough; the workers have to be running too.
        """
        r = rig(_run_container_hook={"side_effect": [None, None, DeployError("hook exploded")]})
        with pytest.raises(DeployError, match="hook exploded"):
            r.orch.deploy(NEW_TAG)

        r.orch.resume_workers.assert_called()

    def test_the_resume_happens_even_when_nothing_is_running(self, rig):
        """The maintenance reset is skipped when frappe is down; the resume must not be."""
        r = rig(running=False, _run_container_hook={"side_effect": DeployError("hook exploded")})
        with pytest.raises(DeployError, match="hook exploded"):
            r.orch.deploy(NEW_TAG)

        r.orch._set_maintenance.assert_not_called()
        r.orch.resume_workers.assert_called()

    def test_a_failing_backup_is_not_swallowed(self, rig):
        r = rig(_backup_all={"side_effect": OSError("disk full")})
        with pytest.raises(OSError, match="disk full"):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_compose.assert_called_once()
        r.orch._migrate.assert_not_called()


# ================================================== deploy: migrate failure


class TestMigrateFailure:
    def test_migrate_failure_keeps_the_old_image_and_never_swaps(self, rig):
        r = rig(_migrate={"side_effect": docker_error("patch blew up")}, deploy_state=DeployState(current_tag=OLD_TAG))
        with pytest.raises(DeployError) as exc:
            r.orch.deploy(NEW_TAG)
        assert f"kept old image ({OLD_TAG})" in str(exc.value)
        assert "no swap performed" in str(exc.value)
        r.orch._rolling_swap.assert_not_called()
        r.orch._restore_compose.assert_called_once_with({"snap": b"x"})
        r.orch._record.assert_not_called()

    def test_migrate_failure_without_a_previous_tag_says_dev_mount(self, rig):
        r = rig(_migrate={"side_effect": docker_error("patch blew up")})
        with pytest.raises(DeployError, match=r"kept old image \(dev/mount\)"):
            r.orch.deploy(NEW_TAG)

    def test_migrate_failure_notifies_instead_of_running_success_hooks(self, rig):
        r = rig(_migrate={"side_effect": docker_error("patch blew up")})
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._notify_after_migrate.assert_called_once_with(NEW_TAG)
        phases = [phase for _n, phase, _v in r.hook_phases()]
        assert "after_migrate" not in phases
        assert "host_after_migrate" not in phases
        assert phases == ["host_before_migrate", "before_migrate"]

    def test_migrate_failure_marks_the_status_for_the_hook_env(self, rig):
        r = rig(_migrate={"side_effect": docker_error("patch blew up")})
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        assert r.orch._migrate_status == "failed"

    def test_rollback_db_restores_the_insurance_dump_on_migrate_failure(self, rig):
        r = rig(
            switch=SwitchConfig(migrate=True, backup_db=True, rollback_db=True),
            _migrate={"side_effect": docker_error("patch blew up")},
        )
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_db.assert_called_once_with(SITE, r.backups[SITE])

    def test_rollback_db_off_leaves_the_database_alone(self, rig):
        r = rig(
            switch=SwitchConfig(migrate=True, rollback_db=False),
            _migrate={"side_effect": docker_error("patch blew up")},
        )
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_db.assert_not_called()

    def test_rollback_db_with_no_dump_taken_restores_nothing(self, rig):
        r = rig(
            switch=SwitchConfig(migrate=True, backup_db="auto", rollback_db=True),
            _migrate={"side_effect": docker_error("patch blew up")},
            _backup_all={"return_value": {}},
        )
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_db.assert_not_called()

    def test_a_migrate_killed_by_its_timeout_takes_the_migrate_failure_path(self, rig):
        """``_migrate`` reports a ``migrate_timeout`` kill as a DeployError, not a
        DockerException. It must still land on THIS handler -- notify + rollback_db -- and not
        fall through to the generic pre-swap abort, which does neither."""
        r = rig(
            switch=SwitchConfig(migrate=True, backup_db=True, rollback_db=True),
            _migrate={"side_effect": DeployError("Migration exceeded [switch].migrate_timeout (42s)")},
        )
        with pytest.raises(DeployError, match="migrate_timeout"):
            r.orch.deploy(NEW_TAG)
        r.orch._notify_after_migrate.assert_called_once_with(NEW_TAG)
        r.orch._restore_db.assert_called_once_with(SITE, r.backups[SITE])
        r.orch._rolling_swap.assert_not_called()
        assert r.orch._migrate_status == "failed"

    def test_a_declined_external_restore_never_masks_the_migrate_error(self, rig):
        r = rig(
            switch=SwitchConfig(migrate=True, backup_db=True, rollback_db=True),
            _migrate={"side_effect": docker_error("patch blew up")},
            _restore_db={"side_effect": RestoreNotConfirmed("not typed")},
        )
        with pytest.raises(DeployError, match="Migration failed") as exc:
            r.orch.deploy(NEW_TAG)
        assert not isinstance(exc.value, RestoreNotConfirmed)
        assert any("not typed" in str(c.args) for c in r.orch.output.warning.call_args_list)


class TestNotifyAfterMigrate:
    """Failure-path notifications: container then host, never fatal."""

    def _notifier(self, tmp_path):
        hooks = SwitchHooks(after_migrate="notify-c", host=SwitchHookScripts(after_migrate="notify-h"))
        orch = make_orch(tmp_path, switch=SwitchConfig(hooks=hooks))
        orch._run_container_hook = MagicMock()
        orch._run_host_hook = MagicMock()
        return orch

    def test_container_hook_runs_before_the_host_hook(self, tmp_path):
        orch = self._notifier(tmp_path)
        orch._notify_after_migrate(NEW_TAG)
        orch._run_container_hook.assert_called_once_with("notify-c", "after_migrate", NEW_TAG)
        orch._run_host_hook.assert_called_once_with("notify-h", "host_after_migrate", NEW_TAG)

    def test_a_broken_notification_hook_cannot_mask_the_migrate_error(self, tmp_path):
        orch = self._notifier(tmp_path)
        orch._run_container_hook = MagicMock(side_effect=DeployError("notifier down"))
        orch._notify_after_migrate(NEW_TAG)  # must not raise
        orch._run_host_hook.assert_called_once()
        assert orch.output.warning.called


# ================================================== deploy: hook call points


class TestHookInvocationPoints:
    def test_every_phase_fires_with_its_configured_script_and_the_new_tag(self, rig):
        hooks = SwitchHooks(
            before_migrate="c-bm",
            after_migrate="c-am",
            before_restart="c-br",
            after_restart="c-ar",
            host=SwitchHookScripts(
                before_migrate="h-bm",
                after_migrate="h-am",
                before_restart="h-br",
                after_restart="h-ar",
            ),
        )
        r = rig(switch=SwitchConfig(hooks=hooks))
        r.orch.deploy(NEW_TAG)
        assert r.hook_phases() == [
            ("_run_host_hook", "host_before_migrate", "h-bm"),
            ("_run_container_hook", "before_migrate", "c-bm"),
            ("_run_container_hook", "after_migrate", "c-am"),
            ("_run_host_hook", "host_after_migrate", "h-am"),
            ("_run_host_hook", "host_before_restart", "h-br"),
            ("_run_container_hook", "before_restart", "c-br"),
            ("_run_container_hook", "after_restart", "c-ar"),
            ("_run_host_hook", "host_after_restart", "h-ar"),
        ]
        assert all(call.args[2] == NEW_TAG for call in r.orch._run_host_hook.call_args_list)

    def test_migrate_hooks_do_not_fire_for_a_code_only_deploy(self, rig):
        r = rig(switch=SwitchConfig(migrate=False))
        r.orch.deploy(NEW_TAG)
        phases = [phase for _n, phase, _v in r.hook_phases()]
        assert phases == ["host_before_restart", "before_restart", "after_restart", "host_after_restart"]

    def test_after_restart_failure_still_records_and_clears_the_page(self, rig):
        """Post-swap: the new image IS live, so bookkeeping must stay truthful."""
        r = rig(_run_container_hook={"side_effect": [None, None, None, DeployError("post hook died")]})
        with pytest.raises(DeployError, match="post hook died"):
            r.orch.deploy(NEW_TAG)
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]
        r.orch._record.assert_called_once_with(NEW_TAG, "migrated", backups=r.backups)
        r.orch._restore_compose.assert_not_called()

    def test_after_restart_failure_without_a_window_skips_the_maintenance_write(self, rig):
        r = rig(
            switch=SwitchConfig(migrate=False),
            _run_container_hook={"side_effect": [None, DeployError("post hook died")]},
        )
        with pytest.raises(DeployError, match="post hook died"):
            r.orch.deploy(NEW_TAG)
        r.orch._set_maintenance.assert_not_called()
        r.orch._record.assert_called_once()


class TestHookRunners:
    def test_empty_hook_value_is_a_noop_for_both_runners(self, tmp_path):
        orch = make_orch(tmp_path)
        orch._frappe_running = MagicMock(return_value=True)
        orch._exec_frappe = MagicMock()
        with patch("subprocess.run") as run:
            orch._run_host_hook(None, "before_restart", NEW_TAG)
            orch._run_host_hook("", "before_restart", NEW_TAG)
        run.assert_not_called()
        orch._run_container_hook(None, "before_restart", NEW_TAG)
        orch._exec_frappe.assert_not_called()

    def test_host_hook_runs_bash_from_the_bench_directory(self, tmp_path):
        orch = make_orch(tmp_path)
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="ok\n", stderr="")) as run:
            orch._run_host_hook("echo ok", "before_restart", NEW_TAG)
        (argv,), kwargs = run.call_args
        assert argv[0] == "bash"
        assert kwargs["cwd"] == str(orch.bench_path)
        assert kwargs["check"] is False
        assert not Path(argv[1]).exists()  # the temp script is always removed

    def test_host_hook_script_carries_the_deploy_env(self, tmp_path):
        orch = make_orch(tmp_path)
        written = {}

        def _capture(argv, **_kw):
            written["body"] = Path(argv[1]).read_text()
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_capture):
            orch._run_host_hook("echo hi", "before_restart", NEW_TAG)
        assert f"export SITE_NAME={SITE}" in written["body"]
        assert f"export DEPLOY_TAG={NEW_TAG}" in written["body"]
        assert f"export BENCH_PATH={orch.bench_path}" in written["body"]

    def test_hook_script_exports_the_migrate_outcome_and_both_log_paths(self, tmp_path):
        """``after_migrate`` fires on success AND failure, so the hook has to be told which one it is, and where the migrate output landed on both sides of the bind mount."""
        orch = make_orch(tmp_path)
        orch._migrate_status = "failed"
        orch._migrate_log_container = "/workspace/frappe-bench/logs/deploy-migrate-1.log"
        orch._migrate_log_host = orch.bench_path / "workspace" / "frappe-bench" / "logs" / "deploy-migrate-1.log"
        script = orch._hook_script("echo hi", NEW_TAG)
        assert "export MIGRATE_STATUS=failed" in script
        assert f"export MIGRATE_LOG_FILE={orch._migrate_log_container}" in script
        assert f"export MIGRATE_LOG_FILE_HOST={orch._migrate_log_host}" in script

    def test_hook_script_omits_the_migrate_vars_when_no_migrate_ran(self, tmp_path):
        script = make_orch(tmp_path)._hook_script("echo hi", NEW_TAG)
        assert "MIGRATE_STATUS" not in script
        assert "MIGRATE_LOG_FILE" not in script

    def test_host_hook_nonzero_exit_is_a_deploy_error_carrying_stderr(self, tmp_path):
        orch = make_orch(tmp_path)
        with (
            patch("subprocess.run", return_value=SimpleNamespace(returncode=7, stdout="", stderr="nope\n")),
            pytest.raises(DeployError, match=r"before_restart hook \(host\) failed \(exit 7\): nope"),
        ):
            orch._run_host_hook("false", "before_restart", NEW_TAG)

    def test_container_hook_is_skipped_when_no_container_is_running(self, tmp_path):
        orch = make_orch(tmp_path)
        orch._frappe_running = MagicMock(return_value=False)
        orch._exec_frappe = MagicMock()
        orch._run_container_hook("echo hi", "before_restart", NEW_TAG)
        orch._exec_frappe.assert_not_called()
        assert orch.output.warning.called

    def test_container_hook_execs_a_script_dropped_in_the_bind_mounted_logs_dir(self, tmp_path):
        orch = make_orch(tmp_path)
        orch._frappe_running = MagicMock(return_value=True)
        seen = {}

        def _exec(command, **_kw):
            seen["command"] = command
            host = orch.bench_path / "workspace" / "frappe-bench" / "logs" / command.split("/")[-1]
            seen["body"] = host.read_text()
            return SimpleNamespace(stdout=["done"])

        orch._exec_frappe = MagicMock(side_effect=_exec)
        orch._run_container_hook("echo hi", "before_restart", NEW_TAG)
        assert seen["command"].startswith("bash /workspace/frappe-bench/logs/.fm_hook_before_restart_")
        assert f"export DEPLOY_TAG={NEW_TAG}" in seen["body"]
        leftovers = list((orch.bench_path / "workspace" / "frappe-bench" / "logs").glob(".fm_hook_*"))
        assert leftovers == []

    def test_container_hook_failure_is_a_deploy_error_and_still_cleans_up(self, tmp_path):
        orch = make_orch(tmp_path)
        orch._frappe_running = MagicMock(return_value=True)
        orch._exec_frappe = MagicMock(side_effect=docker_error("exit 1"))
        with pytest.raises(DeployError, match=r"before_restart hook \(container\) failed"):
            orch._run_container_hook("echo hi", "before_restart", NEW_TAG)
        leftovers = list((orch.bench_path / "workspace" / "frappe-bench" / "logs").glob(".fm_hook_*"))
        assert leftovers == []


# ========================================================= deploy: the swap


class TestSwapSelection:
    def test_migrate_under_a_maintenance_window_still_rolls(self, rig):
        r = rig(switch=SwitchConfig(migrate=True, maintenance_mode=True))
        r.orch.deploy(NEW_TAG)
        r.orch._rolling_swap.assert_called_once_with(NEW_TAG, None, {"snap": b"x"})
        r.orch.docker.compose.up.assert_not_called()
        r.orch._ensure_nginx.assert_not_called()

    def test_migrate_without_the_window_must_recreate(self, rig):
        r = rig(switch=SwitchConfig(migrate=True, maintenance_mode=False))
        r.orch.deploy(NEW_TAG)
        r.orch._rolling_swap.assert_not_called()
        r.orch.docker.compose.up.assert_called_once_with(services=[], detach=True, pull="never", stream=False)
        r.orch._up_workers.assert_called_once()
        r.orch._ensure_nginx.assert_called_once()

    def test_additive_assertion_empty_phases_rolls_without_the_window(self, rig):
        r = rig(switch=SwitchConfig(migrate=True, maintenance_mode=False, maintenance_mode_phases=[]))
        r.orch.deploy(NEW_TAG)
        r.orch._rolling_swap.assert_called_once()

    def test_no_rolling_override_forces_the_recreate_swap(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG, rolling=False)
        r.orch._rolling_swap.assert_not_called()
        r.orch.docker.compose.up.assert_called_once()

    def test_rolling_override_forces_rolling_for_a_bare_migrate(self, rig):
        r = rig(switch=SwitchConfig(migrate=True, maintenance_mode=False))
        r.orch.deploy(NEW_TAG, rolling=True)
        r.orch._rolling_swap.assert_called_once()

    def test_rolling_needs_a_running_web_tier_and_says_so(self, rig):
        r = rig(running=False)
        r.orch.deploy(NEW_TAG, rolling=True)
        r.orch._rolling_swap.assert_not_called()
        r.orch.docker.compose.up.assert_called_once()
        assert any("no running web to swap alongside" in str(c.args) for c in r.orch.output.warning.call_args_list)

    def test_rolling_swap_is_handed_the_old_tag_and_the_snapshots(self, rig):
        r = rig(deploy_state=DeployState(current_tag=OLD_TAG))
        r.orch.deploy(NEW_TAG)
        r.orch._rolling_swap.assert_called_once_with(NEW_TAG, OLD_TAG, {"snap": b"x"})


# ================================================ deploy: swap failure unwind


class TestSwapFailureUnwind:
    """A failure IN the swap is an abort window too: page off, workers back.

    The pre-swap handler's ``try`` closes before the swap runs, so a swap that raises (a rolling
    swap refusing after an unhealthy new replica, a recreate ``compose up`` that cannot start)
    escaped with ``maintenance_mode`` still 1 and RQ still suspended: the surviving OLD stack
    served 503 to every visitor while the CLI reported that the old image was kept -- the exact
    opposite of "zero downtime even on a failed rolling deploy".
    """

    def test_rolling_swap_failure_clears_maintenance_and_resumes_workers(self, rig):
        r = rig(_rolling_swap={"side_effect": DeployError("new frappe replica failed health check")})
        with pytest.raises(DeployError, match="new frappe replica failed health check"):
            r.orch.deploy(NEW_TAG)
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]
        r.orch.resume_workers.assert_called()
        r.orch._record.assert_not_called()

    def test_the_swap_window_leaves_the_compose_to_the_swap_itself(self, rig):
        """Only the page and the workers unwind here: the rolling swap's own ``_abort_rolling``
        already restored the canonical compose, and a recreate swap is pinned to the tag it
        brought up."""
        r = rig(_rolling_swap={"side_effect": DeployError("boom")})
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_compose.assert_not_called()

    def test_a_failing_recreate_swap_also_drops_the_page_and_resumes(self, rig):
        r = rig(compose_up={"side_effect": docker_error("no such image")})
        with pytest.raises(DockerException):
            r.orch.deploy(NEW_TAG, rolling=False)
        assert [a[0] for a, _k in r.calls("_set_maintenance")] == [1, 0]
        r.orch.resume_workers.assert_called()
        r.orch._record.assert_not_called()

    def test_a_swap_failure_with_nothing_running_still_resumes_the_workers(self, rig):
        """No web to 503 (nothing running, so the window never opened), but the drain's
        PERSISTENT RQ suspend still has to be lifted."""
        r = rig(running=False, compose_up={"side_effect": docker_error("boom")})
        with pytest.raises(DockerException):
            r.orch.deploy(NEW_TAG)
        r.orch._set_maintenance.assert_not_called()
        r.orch.resume_workers.assert_called()


# =================================================== deploy: the health gate


class TestHealthGate:
    def test_unhealthy_new_image_rolls_back_to_the_previous_tag(self, rig):
        r = rig(deploy_state=DeployState(current_tag=OLD_TAG), _health_check=False)
        with pytest.raises(DeployError, match=f"failed health check; rolled back to {OLD_TAG}"):
            r.orch.deploy(NEW_TAG)
        r.orch.rollback.assert_called_once_with(OLD_TAG, restore_db_dumps=None)

    def test_rollback_db_hands_the_dump_to_the_rollback(self, rig):
        r = rig(
            switch=SwitchConfig(backup_db=True, rollback_db=True),
            deploy_state=DeployState(current_tag=OLD_TAG),
            _health_check=False,
        )
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch.rollback.assert_called_once_with(OLD_TAG, restore_db_dumps=r.backups)

    def test_no_previous_tag_halts_the_bench_in_maintenance(self, rig):
        r = rig(_health_check=False)
        with pytest.raises(DeployError, match="halted in maintenance mode"):
            r.orch.deploy(NEW_TAG)
        r.orch.rollback.assert_not_called()
        r.orch._set_maintenance.assert_called_once_with(1)

    def test_rollback_image_disabled_halts_rather_than_reverting(self, rig):
        r = rig(
            switch=SwitchConfig(rollback_image=False),
            deploy_state=DeployState(current_tag=OLD_TAG),
            _health_check=False,
        )
        with pytest.raises(DeployError, match="halted in maintenance mode"):
            r.orch.deploy(NEW_TAG)
        r.orch.rollback.assert_not_called()

    def test_a_failed_health_gate_never_records_the_new_tag(self, rig):
        r = rig(_health_check=False)
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._record.assert_not_called()

    def test_a_failed_health_gate_does_not_restore_the_compose(self, rig):
        """Post-swap: the compose IS the new tag and the rollback re-pins it."""
        r = rig(deploy_state=DeployState(current_tag=OLD_TAG), _health_check=False)
        with pytest.raises(DeployError):
            r.orch.deploy(NEW_TAG)
        r.orch._restore_compose.assert_not_called()

    def test_nginx_is_only_nudged_on_the_recreate_path(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG, rolling=False)
        r.orch._ensure_nginx.assert_called_once()


# ====================================================== deploy: finalize/record


class TestFinalizeAndRecord:
    def test_finalize_resumes_installs_merges_then_clears_cache(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG)
        order = r.order
        assert (
            order.index("resume_workers")
            < order.index("_install_new_apps")
            < order.index("_apply_config_merges")
            < order.index("_exec_frappe")
        )
        (command,), _ = r.orch._exec_frappe.call_args
        assert command.endswith(f"--site {SITE} clear-cache")
        r.orch._exec_frappe.assert_called_once()

    def test_finalize_clears_the_cache_for_every_site(self, rig):
        """The cache is keyed per schema, so a site whose cache still holds the OLD image's
        doctype meta serves stale definitions under the new code."""
        r = rig(site_names=[SITE, SITE2])
        r.orch.deploy(NEW_TAG)
        assert [a[0] for a, _k in r.calls("_exec_frappe")] == [
            f"{BENCH_BIN} --site {SITE} clear-cache",
            f"{BENCH_BIN} --site {SITE2} clear-cache",
        ]

    def test_one_sites_failing_clear_cache_does_not_skip_the_next(self, rig):
        """It is a warning, not a gate: the deploy is already live."""
        r = rig(site_names=[SITE, SITE2], _exec_frappe={"side_effect": [docker_error("no container"), None]})
        r.orch.deploy(NEW_TAG)
        assert [a[0] for a, _k in r.calls("_exec_frappe")] == [
            f"{BENCH_BIN} --site {SITE} clear-cache",
            f"{BENCH_BIN} --site {SITE2} clear-cache",
        ]
        r.orch._record.assert_called_once()

    def test_a_failing_clear_cache_only_warns(self, rig):
        r = rig(_exec_frappe={"side_effect": docker_error("no container")})
        r.orch.deploy(NEW_TAG)
        r.orch._record.assert_called_once()
        assert any("clear-cache failed" in str(c.args) for c in r.orch.output.warning.call_args_list)

    def test_record_carries_the_migrate_status_and_the_dump_path(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG)
        r.orch._record.assert_called_once_with(NEW_TAG, "migrated", backups=r.backups)

    def test_prune_only_runs_when_keep_was_asked_for(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG)
        r.orch.prune_releases.assert_not_called()
        r.orch.deploy(NEW_TAG, prune_keep=3)
        r.orch.prune_releases.assert_called_once_with(keep=3)

    def test_prune_zero_is_still_a_request(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG, prune_keep=0)
        r.orch.prune_releases.assert_called_once_with(keep=0)

    def test_a_failing_prune_never_fails_a_good_deploy(self, rig):
        r = rig(prune_releases={"side_effect": RuntimeError("rmi refused")})
        r.orch.deploy(NEW_TAG, prune_keep=1)
        assert any("Release prune failed" in str(c.args) for c in r.orch.output.warning.call_args_list)

    def test_prune_runs_after_the_deploy_is_recorded(self, rig):
        r = rig()
        r.orch.deploy(NEW_TAG, prune_keep=2)
        assert r.order.index("_record") < r.order.index("prune_releases")


class TestRecordBookkeeping:
    def test_record_rotates_current_into_previous_and_appends_history(self, tmp_path):
        state = DeployState(
            current_tag=OLD_TAG,
            history=[
                DeployStateEntry(tag=OLD_TAG, deployed_at="t0", migrate_status="migrated"),
            ],
        )
        orch = make_orch(tmp_path, deploy_state=state)
        orch._record(NEW_TAG, "migrated", backups={SITE: Path("/b/db.sql")})
        result = orch.config.deploy_state
        assert result.previous_tag == OLD_TAG
        assert result.current_tag == NEW_TAG
        assert [e.tag for e in result.history] == [OLD_TAG, NEW_TAG]
        assert result.history[-1].backups == {SITE: "/b/db.sql"}
        assert result.history[-1].migrate_status == "migrated"
        assert result.last_deploy_at == result.history[-1].deployed_at
        orch.config.export_to_toml.assert_called_once_with(Path(orch.config.root_path))

    def test_record_on_a_virgin_bench_creates_the_state(self, tmp_path):
        orch = make_orch(tmp_path, deploy_state=None)
        orch._record(NEW_TAG, "skipped")
        result = orch.config.deploy_state
        assert result.previous_tag is None
        assert result.current_tag == NEW_TAG
        assert result.history[-1].backups == {}

    def test_re_recording_the_current_tag_keeps_the_older_previous(self, tmp_path):
        """``previous_tag`` must not collapse onto ``current_tag``.

        The health-gate rollback records the tag that is ALREADY current (it re-pinned the running
        old image). Rotating it into ``previous_tag`` would make the operator's next escape hatch,
        ``fm switch --previous``, a redeploy of what is already live and strand the genuinely older
        release.
        """
        older = "reg.example/shop:v0"
        state = DeployState(
            current_tag=OLD_TAG,
            previous_tag=older,
            history=[DeployStateEntry(tag=OLD_TAG, deployed_at="t0", migrate_status="migrated")],
        )
        orch = make_orch(tmp_path, deploy_state=state)
        orch._record(OLD_TAG, "rollback")
        result = orch.config.deploy_state
        assert result.current_tag == OLD_TAG
        assert result.previous_tag == older
        assert [e.tag for e in result.history] == [OLD_TAG, OLD_TAG]

    def test_current_deployed_tag_reads_the_recorded_tag(self, tmp_path):
        assert make_orch(tmp_path)._current_deployed_tag() is None
        assert make_orch(tmp_path, deploy_state=DeployState())._current_deployed_tag() is None
        state = DeployState(current_tag=OLD_TAG)
        assert make_orch(tmp_path, deploy_state=state)._current_deployed_tag() == OLD_TAG

    def test_every_sites_dump_is_recorded_not_only_the_primarys(self, tmp_path):
        """``backups`` is what a later ``fm switch --previous --restore-db`` reads back.

        Recording one entry for a bench that dumped N schemas makes that restore silently
        incomplete: it succeeds, says nothing, and leaves every unrecorded site on the data
        the release being abandoned wrote.
        """
        orch = make_orch(tmp_path, site_names=[SITE, SITE2])
        orch._record(
            NEW_TAG,
            "migrated",
            backups={SITE: Path("/b/db-shopdb.sql"), SITE2: Path("/b/db-annexdb.sql")},
        )
        entry = orch.config.deploy_state.history[-1]
        assert entry.backups == {SITE: "/b/db-shopdb.sql", SITE2: "/b/db-annexdb.sql"}

    def test_a_two_site_deploy_records_a_dump_per_site_as_strings(self, rig):
        """End to end: the dumps ``_backup_all`` returned are the dumps history carries, and
        they are stored as ``str`` because that is what the model holds."""
        r = rig(site_names=[SITE, SITE2])
        del r.orch._record  # the real bookkeeping is what this pins
        r.orch.deploy(NEW_TAG)
        entry = r.orch.config.deploy_state.history[-1]
        assert entry.backups == {site: str(path) for site, path in r.backups.items()}
        assert sorted(entry.backups) == sorted([SITE, SITE2])
        assert all(isinstance(value, str) for value in entry.backups.values())


# ================================================================== rollback


class TestRollback:
    """The internal health-gate recovery: minimal by design."""

    def _rollback_rig(self, tmp_path, switch=None, deploy_state=None, healthy=True, site_names=None):
        orch = make_orch(tmp_path, switch=switch, deploy_state=deploy_state, site_names=site_names)
        manager = MagicMock()
        for name, result in (
            ("_fetch_image", {}),
            ("_pin_workers", {}),
            ("_restore_db", {}),
            ("_up_workers", {}),
            ("_health_check", {"return_value": healthy}),
            ("_ensure_nginx", {}),
            ("resume_workers", {}),
            ("_exec_frappe", {}),
            ("_record", {}),
            ("drain_workers", {"return_value": True}),
            ("_backup_all", {}),
            ("_run_host_hook", {}),
            ("_run_container_hook", {}),
            ("_set_maintenance", {}),
        ):
            spy = MagicMock(**result)
            manager.attach_mock(spy, name)
            setattr(orch, name, spy)
        for owner, attr, label in (
            (orch.docker.compose, "up", "compose_up"),
            (orch.docker_ops, "render_image_compose", "render_image_compose"),
        ):
            spy = MagicMock()
            manager.attach_mock(spy, label)
            setattr(owner, attr, spy)
        return orch, manager

    def test_rollback_repins_the_previous_tag_and_recreates(self, tmp_path):
        orch, manager = self._rollback_rig(tmp_path)
        orch.rollback(OLD_TAG)
        assert [n for n, _a, _k in manager.mock_calls] == [
            "_fetch_image",
            "render_image_compose",
            "_pin_workers",
            "compose_up",
            "_up_workers",
            "_health_check",
            "_ensure_nginx",
            "resume_workers",
            "_exec_frappe",
            "_record",
        ]
        orch.docker_ops.render_image_compose.assert_called_once_with(OLD_TAG)
        orch._pin_workers.assert_called_once_with(OLD_TAG)
        orch._record.assert_called_once_with(OLD_TAG, "rollback")

    def test_rollback_never_drains_backs_up_migrates_or_hooks(self, tmp_path):
        orch, _ = self._rollback_rig(tmp_path)
        orch.rollback(OLD_TAG)
        orch.drain_workers.assert_not_called()
        orch._backup_all.assert_not_called()
        orch._run_host_hook.assert_not_called()
        orch._run_container_hook.assert_not_called()

    def test_rollback_clears_maintenance_mode_globally(self, tmp_path):
        orch, _ = self._rollback_rig(tmp_path)
        orch.rollback(OLD_TAG)
        (command,), _ = orch._exec_frappe.call_args
        assert command.endswith(f"--site {SITE} set-config -g maintenance_mode 0")

    def test_a_failing_maintenance_clear_only_warns(self, tmp_path):
        orch, _ = self._rollback_rig(tmp_path)
        orch._exec_frappe = MagicMock(side_effect=docker_error("gone"))
        orch.rollback(OLD_TAG)
        orch._record.assert_called_once_with(OLD_TAG, "rollback")
        assert any("Could not clear maintenance mode" in str(c.args) for c in orch.output.warning.call_args_list)

    def test_rollback_imports_the_dump_before_the_swap(self, tmp_path):
        orch, manager = self._rollback_rig(tmp_path)
        dump = tmp_path / "db.sql"
        orch.rollback(OLD_TAG, restore_db_dumps={SITE: dump})
        names = [n for n, _a, _k in manager.mock_calls]
        orch._restore_db.assert_called_once_with(SITE, dump)
        assert names.index("_restore_db") < names.index("compose_up")

    def test_a_declined_dump_import_still_rolls_the_image_back(self, tmp_path):
        orch, _ = self._rollback_rig(tmp_path)
        orch._restore_db = MagicMock(side_effect=RestoreNotConfirmed("not typed"))
        orch.rollback(OLD_TAG, restore_db_dumps={SITE: tmp_path / "db.sql"})
        orch.docker.compose.up.assert_called_once()
        orch._record.assert_called_once_with(OLD_TAG, "rollback")
        assert any("not typed" in str(c.args) for c in orch.output.warning.call_args_list)

    def test_every_site_in_the_dump_set_is_restored_with_its_own_dump(self, tmp_path):
        """``restore_db_dumps`` maps SITE to a dump, and the loop owes every entry a restore.

        The health gate hands back the insurance dumps for the WHOLE bench. Restoring only the
        first of them would re-pin the old image over a bench where one schema went back and
        the others stayed on the data the failed release wrote: two points in time under one
        code base, and no second dump left to fix it with.
        """
        orch, _ = self._rollback_rig(tmp_path, site_names=[SITE, SITE2])
        dumps = {SITE: tmp_path / "shop.sql", SITE2: tmp_path / "annex.sql"}
        orch.rollback(OLD_TAG, restore_db_dumps=dumps)
        assert [c.args for c in orch._restore_db.call_args_list] == [
            (SITE, dumps[SITE]),
            (SITE2, dumps[SITE2]),
        ]

    def test_one_declined_import_neither_stops_the_next_site_nor_the_image_rollback(self, tmp_path):
        """Declining is a decision about ONE database, not about the bench's image."""
        orch, _ = self._rollback_rig(tmp_path, site_names=[SITE, SITE2])
        orch._restore_db = MagicMock(side_effect=[RestoreNotConfirmed("not typed"), None])
        dumps = {SITE: tmp_path / "shop.sql", SITE2: tmp_path / "annex.sql"}
        orch.rollback(OLD_TAG, restore_db_dumps=dumps)
        assert [c.args for c in orch._restore_db.call_args_list] == [
            (SITE, dumps[SITE]),
            (SITE2, dumps[SITE2]),
        ]
        assert any("not typed" in str(c.args) for c in orch.output.warning.call_args_list)
        orch.docker.compose.up.assert_called_once()
        orch._record.assert_called_once_with(OLD_TAG, "rollback")

    def test_an_unhealthy_rollback_still_records_the_pinned_reality(self, tmp_path):
        orch, _ = self._rollback_rig(tmp_path, healthy=False)
        with pytest.raises(DeployError, match=f"Rollback to {OLD_TAG} failed health check"):
            orch.rollback(OLD_TAG)
        orch._record.assert_called_once_with(OLD_TAG, "rollback")
        orch.resume_workers.assert_not_called()
        orch._ensure_nginx.assert_not_called()

    def test_rollback_refuses_a_non_image_bench(self, tmp_path):
        orch, _ = self._rollback_rig(tmp_path)
        orch.config.runtime = BenchRuntime.mount
        with pytest.raises(DeployError, match="not in image runtime"):
            orch.rollback(OLD_TAG)
        orch._fetch_image.assert_not_called()

    def test_the_health_gate_rollback_preserves_the_previous_tag(self, tmp_path):
        """After the auto-rollback, ``fm switch --previous`` must still reach the older release."""
        older = "reg.example/shop:v0"
        state = DeployState(
            current_tag=OLD_TAG,
            previous_tag=older,
            history=[
                DeployStateEntry(tag=older, deployed_at="t0", migrate_status="skipped"),
                DeployStateEntry(tag=OLD_TAG, deployed_at="t1", migrate_status="migrated"),
            ],
        )
        orch, _ = self._rollback_rig(tmp_path, deploy_state=state)
        del orch._record  # the real bookkeeping is what this pins
        orch.rollback(OLD_TAG)
        result = orch.config.deploy_state
        assert result.current_tag == OLD_TAG
        assert result.previous_tag == older


class TestRollingRestart:
    def _restart_rig(self, tmp_path, deploy_state=None, running=True):
        orch = make_orch(tmp_path, deploy_state=deploy_state)
        orch._frappe_running = MagicMock(return_value=running)
        orch._snapshot_compose = MagicMock(return_value={"snap": b"x"})
        orch._fetch_image = MagicMock()
        orch._rolling_swap = MagicMock()
        orch._record = MagicMock()
        return orch

    def test_rolling_restart_reuses_the_current_tag_on_both_sides(self, tmp_path):
        orch = self._restart_rig(tmp_path, DeployState(current_tag=OLD_TAG))
        orch.rolling_restart()
        orch._fetch_image.assert_called_once_with(OLD_TAG)
        orch._rolling_swap.assert_called_once_with(OLD_TAG, OLD_TAG, {"snap": b"x"})
        orch._record.assert_not_called()

    def test_rolling_restart_refuses_an_unrecorded_bench(self, tmp_path):
        orch = self._restart_rig(tmp_path, DeployState())
        with pytest.raises(DeployError, match="No deployed image tag recorded"):
            orch.rolling_restart()
        orch._rolling_swap.assert_not_called()

    def test_rolling_restart_refuses_a_stopped_web_tier(self, tmp_path):
        orch = self._restart_rig(tmp_path, DeployState(current_tag=OLD_TAG), running=False)
        with pytest.raises(DeployError, match="Web tier is not running"):
            orch.rolling_restart()
        orch._rolling_swap.assert_not_called()


# ======================================== the destructive-restore confirmation


class TestRestoreConfirmation:
    """``_restore_db`` / ``_confirm_restore``: what is REFUSED."""

    def _restorer(self, tmp_path, external=True, interactive=True, answer="shopdb"):
        orch = make_orch(tmp_path)
        if external:
            orch.config.database[SITE] = SimpleNamespace(host="db.example", port=3306)
        manager = MagicMock()
        manager.database_server_info = SimpleNamespace(host="db.example", port=3306)
        manager.db_run_query.return_value = SimpleNamespace(stdout=["1\t42"])
        orch._db_manager = MagicMock(return_value=(manager, "shopdb"))
        orch.output.is_interactive.return_value = interactive
        orch.output.prompt_ask.return_value = answer
        dump = tmp_path / "dump.sql"
        dump.write_text("-- dump")
        return orch, manager, dump

    def test_a_missing_dump_is_a_warning_not_an_import(self, tmp_path):
        orch, manager, _ = self._restorer(tmp_path)
        orch._restore_db(SITE, tmp_path / "absent.sql")
        manager.db_import.assert_not_called()
        assert orch.output.warning.called

    def test_an_unresolvable_db_name_refuses_the_import(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path)
        orch._db_manager = MagicMock(return_value=(manager, None))
        orch._restore_db(SITE, dump)
        manager.db_import.assert_not_called()

    def test_the_global_db_is_confirmed_like_any_other_schema(self, tmp_path):
        """fm owning the container is not a reason to drop its tables unasked: the
        operator loses the same site data either way, so the typed-name question is
        the same. Only the wording naming the owner differs."""
        orch, manager, dump = self._restorer(tmp_path, external=False)
        orch._restore_db(SITE, dump)
        orch.output.prompt_ask.assert_called_once()
        manager.db_run_query.assert_called_once()
        manager.db_import.assert_called_once_with("shopdb", dump, force=True)
        warned = " ".join(str(c.args) for c in orch.output.warning.call_args_list)
        assert "fm's own global-db container" in warned

    def test_typing_the_schema_name_authorises_the_overwrite(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path, answer=" shopdb ")
        orch._restore_db(SITE, dump)
        manager.db_import.assert_called_once_with("shopdb", dump, force=True)

    def test_a_wrong_answer_refuses_the_import_entirely(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path, answer="yes")
        with pytest.raises(RestoreNotConfirmed, match="Nothing was imported"):
            orch._restore_db(SITE, dump)
        manager.db_import.assert_not_called()

    def test_the_prompt_quotes_the_live_table_count(self, tmp_path):
        orch, _manager, dump = self._restorer(tmp_path)
        orch._restore_db(SITE, dump)
        warned = " ".join(str(c.args) for c in orch.output.warning.call_args_list)
        assert "it holds 42 tables right now" in warned

    def test_an_absent_schema_is_described_as_a_create(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path)
        manager.db_run_query.return_value = SimpleNamespace(stdout=["0\t0"])
        orch._restore_db(SITE, dump)
        warned = " ".join(str(c.args) for c in orch.output.warning.call_args_list)
        assert "does not exist on that server yet" in warned

    def test_an_unreadable_count_still_demands_the_typed_name(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path, answer="nope")
        manager.db_run_query.side_effect = RuntimeError("TLS handshake failed")
        with pytest.raises(RestoreNotConfirmed):
            orch._restore_db(SITE, dump)
        warned = " ".join(str(c.args) for c in orch.output.warning.call_args_list)
        assert "could not read how many tables" in warned

    def test_non_interactive_imports_unconfirmed_without_querying(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path, interactive=False)
        orch._restore_db(SITE, dump)
        orch.output.prompt_ask.assert_not_called()
        manager.db_run_query.assert_not_called()
        manager.db_import.assert_called_once_with("shopdb", dump, force=True)

    def test_a_promptless_output_mode_imports_unconfirmed(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path)
        orch.output.prompt_ask.side_effect = NonInteractiveError("json mode")
        orch._restore_db(SITE, dump)
        manager.db_import.assert_called_once_with("shopdb", dump, force=True)

    def test_an_external_import_failure_carries_the_tls_hint(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path)
        manager.db_import.side_effect = RuntimeError("access denied")
        with pytest.raises(DeployError, match=r"ca-bundle\.pem"):
            orch._restore_db(SITE, dump)

    def test_a_global_db_import_failure_propagates_unwrapped(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path, external=False)
        manager.db_import.side_effect = RuntimeError("access denied")
        with pytest.raises(RuntimeError, match="access denied"):
            orch._restore_db(SITE, dump)


# ============================================================ new-app install


class TestInstallNewApps:
    """The baked set is read from the IMAGE, not from bench config.

    ``config.apps_list`` is filled in memory by a bake and deliberately never
    persisted, so once ``fm bake`` and ``fm switch`` became separate commands a
    config-sourced list was always empty on a switch and nothing was ever
    reconciled. Finalize now asks the new container what it carries, which means
    two probes: the ``apps/`` listing (the image) and ``bench list-apps`` (the
    site). ``apps/`` is used rather than ``sites/apps.txt`` because that file is a
    host bind in image runtime, so the image's own copy is shadowed inside the
    container.
    """

    APPS_LS = "ls -1 /workspace/frappe-bench/apps"

    def _installer(self, tmp_path, switch=None, baked=(), listed="", site_names=None):
        """``baked`` is what the image's ``apps/`` listing returns; ``listed`` is the
        ``bench list-apps`` output, either one string used for every site or a
        ``{site: output}`` mapping when the sites carry different apps."""
        orch = make_orch(tmp_path, switch=switch or SwitchConfig(), site_names=site_names)
        per_site = listed if isinstance(listed, dict) else dict.fromkeys(orch.sites, listed)

        def _exec(command, user="frappe"):
            if command == self.APPS_LS:
                return SimpleNamespace(stdout=list(baked))
            site = command.split("--site ", 1)[1].split()[0]
            return SimpleNamespace(stdout=per_site[site].splitlines())

        orch._exec_frappe = MagicMock(side_effect=_exec)
        return orch

    def _installed(self, orch):
        return [c.args[0] for c in orch._exec_frappe.call_args_list if "install-app" in c.args[0]]

    def _installs(self, orch):
        """``(site, app)`` per issued ``bench --site <site> install-app <app>``."""
        return [(c.split("--site ", 1)[1].split()[0], c.split()[-1]) for c in self._installed(orch)]

    def test_install_apps_disabled_reconciles_nothing(self, tmp_path):
        orch = self._installer(tmp_path, SwitchConfig(install_apps=False), ("erpnext",), "frappe\n")
        orch._install_new_apps()
        orch._exec_frappe.assert_not_called()

    def test_the_image_is_asked_what_it_carries(self, tmp_path):
        orch = self._installer(tmp_path, baked=("frappe", "hrms"), listed="frappe\n")
        orch._install_new_apps()
        assert orch._exec_frappe.call_args_list[0].args[0] == self.APPS_LS

    def test_an_image_with_no_readable_apps_reconciles_nothing(self, tmp_path):
        orch = self._installer(tmp_path, baked=(), listed="frappe\n")
        orch._install_new_apps()
        assert self._installed(orch) == []

    def test_an_unreadable_site_app_list_skips_rather_than_reinstalling(self, tmp_path):
        """No 'frappe' in the output means the parse is untrustworthy."""
        orch = self._installer(tmp_path, baked=("erpnext",), listed="Traceback (most recent call last):")
        orch._install_new_apps()
        assert self._installed(orch) == []
        assert any("skipping new-app install" in str(c.args) for c in orch.output.warning.call_args_list)

    def test_only_the_missing_apps_are_installed(self, tmp_path):
        orch = self._installer(tmp_path, baked=("erpnext", "hrms", "payments"), listed="frappe\nerpnext\n")
        orch._install_new_apps()
        commands = self._installed(orch)
        assert [c.split()[-1] for c in commands] == ["hrms", "payments"]

    def test_nothing_missing_means_no_install_call(self, tmp_path):
        orch = self._installer(tmp_path, baked=("erpnext",), listed="frappe\nerpnext\n")
        orch._install_new_apps()
        assert self._installed(orch) == []

    def test_a_failed_install_aborts_finalize_loudly(self, tmp_path):
        orch = self._installer(tmp_path, baked=("hrms",), listed="frappe\n")
        orch._exec_frappe = MagicMock(
            side_effect=[
                SimpleNamespace(stdout=["hrms"]),
                SimpleNamespace(stdout=["frappe"]),
                docker_error("app not found"),
            ],
        )
        with pytest.raises(DeployError, match="Failed to install new app 'hrms'"):
            orch._install_new_apps()

    def test_each_site_gets_only_the_apps_it_is_missing(self, tmp_path):
        """Sites of one bench can carry different apps (``fm create BENCH/SITE --apps``), so the
        install set is computed against each site's OWN ``bench list-apps`` rather than once for
        the bench: a set computed from the primary would reinstall on one site what it is missing
        on the other, and skip what it actually needs. The image is still asked only once."""
        orch = self._installer(
            tmp_path,
            baked=("erpnext", "hrms"),
            listed={SITE: "frappe\nerpnext\n", SITE2: "frappe\nhrms\n"},
            site_names=[SITE, SITE2],
        )
        orch._install_new_apps()
        assert self._installs(orch) == [(SITE, "hrms"), (SITE2, "erpnext")]
        assert [c.args[0] for c in orch._exec_frappe.call_args_list].count(self.APPS_LS) == 1

    def test_one_site_with_an_unreadable_app_list_does_not_skip_the_others(self, tmp_path):
        """The untrustworthy parse is that site's problem alone."""
        orch = self._installer(
            tmp_path,
            baked=("hrms",),
            listed={SITE: "Traceback (most recent call last):", SITE2: "frappe\n"},
            site_names=[SITE, SITE2],
        )
        orch._install_new_apps()
        assert self._installs(orch) == [(SITE2, "hrms")]


class TestConfigMerges:
    def test_no_configured_keys_touches_neither_file(self, tmp_path):
        orch = make_orch(tmp_path)
        orch._apply_config_merges()
        orch.bench.set_common_bench_config.assert_not_called()
        orch.bench.set_bench_site_config.assert_not_called()

    def test_each_configured_block_is_merged_into_its_own_file(self, tmp_path):
        orch = make_orch(
            tmp_path,
            switch=SwitchConfig(common_site_config={"a": 1}, site_config={"b": 2}),
        )
        orch._apply_config_merges()
        orch.bench.set_common_bench_config.assert_called_once_with({"a": 1})
        orch.bench.set_bench_site_config.assert_called_once_with(SITE, {"b": 2})

    def test_site_config_keys_are_merged_into_every_site(self, tmp_path):
        """These keys are part of the deploy, so applying them to one site and not another would
        leave the bench running one image under two configurations. ``common_site_config`` is
        bench-wide and is still written exactly once."""
        orch = make_orch(
            tmp_path,
            switch=SwitchConfig(common_site_config={"a": 1}, site_config={"b": 2}),
            site_names=[SITE, SITE2],
        )
        orch._apply_config_merges()
        assert [c.args for c in orch.bench.set_bench_site_config.call_args_list] == [
            (SITE, {"b": 2}),
            (SITE2, {"b": 2}),
        ]
        orch.bench.set_common_bench_config.assert_called_once_with({"a": 1})


# ============================================================ the rolling swap


@pytest.fixture
def no_sleep():
    with patch("frappe_manager.site_manager.modules.deploy_orchestrator.time.sleep"):
        yield


@pytest.mark.usefixtures("no_sleep")
class TestRollingSwap:
    """Overlap swap: the OLD replica is only torn down once the NEW one is healthy."""

    def _swap(self, tmp_path, frappe_ok=True, nginx_ok=True, new_frappe="newF", new_nginx="newN", scale_error=None):
        orch = make_orch(tmp_path)
        orch.compose.get_services_list.return_value = ["frappe", "nginx", "socketio", "schedule"]
        orch.compose.get_container_names.return_value = {"frappe": "shop-frappe", "nginx": "shop-nginx"}
        olds = {"frappe": ["oldF"], "nginx": ["oldN"]}
        news = {"frappe": [new_frappe] if new_frappe else [], "nginx": [new_nginx] if new_nginx else []}
        seen = {"frappe": 0, "nginx": 0}

        def _ps(service):
            seen[service] += 1
            return olds[service] if seen[service] == 1 else olds[service] + news[service]

        orch._compose_ps_ids = MagicMock(side_effect=_ps)
        orch._container_health = MagicMock(
            side_effect=lambda cid, **_kw: {"newF": frappe_ok, "newN": nginx_ok}.get(cid, False),
        )
        orch._raw_docker = MagicMock()
        # ``scale_error`` keeps the REAL ``_scale`` and fails the compose call underneath it, so the
        # test exercises how a failed ``compose --scale`` is reported and unwound.
        orch._raw_compose = MagicMock(side_effect=scale_error)
        if scale_error is None:
            orch._scale = MagicMock()
        orch._pin_workers = MagicMock()
        orch._up_workers = MagicMock()
        orch._restore_compose = MagicMock()
        return orch

    def _docker_argv(self, orch):
        """``(verb, container)`` per raw-docker call, ignoring the ``rm -f`` flag."""
        return [tuple(a for a in c.args if a != "-f")[:2] for c in orch._raw_docker.call_args_list]

    def test_frappe_is_scaled_before_nginx(self, tmp_path):
        orch = self._swap(tmp_path)
        orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        assert [c.args[0] for c in orch._scale.call_args_list] == [
            {"frappe": 2},
            {"frappe": 2, "nginx": 2},
        ]

    def test_scalable_compose_is_rendered_then_the_canonical_one(self, tmp_path):
        orch = self._swap(tmp_path)
        orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        assert [c.kwargs["rolling"] for c in orch.docker_ops.render_image_compose.call_args_list] == [True, False]
        assert all(c.args[0] == NEW_TAG for c in orch.docker_ops.render_image_compose.call_args_list)

    def test_old_replicas_stop_before_they_are_removed_and_nginx_reloads_between(self, tmp_path):
        orch = self._swap(tmp_path)
        orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        assert self._docker_argv(orch) == [
            ("stop", "oldN"),
            ("exec", "newN"),  # reload: survivor re-resolves the frappe upstream
            ("rm", "oldN"),
            ("stop", "oldF"),
            ("exec", "newN"),
            ("rm", "oldF"),
            ("exec", "newN"),
            ("rename", "newF"),
            ("rename", "newN"),
        ]

    def test_survivors_are_renamed_back_to_the_canonical_names(self, tmp_path):
        orch = self._swap(tmp_path)
        orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        renames = [c.args for c in orch._raw_docker.call_args_list if c.args[0] == "rename"]
        assert renames == [("rename", "newF", "shop-frappe"), ("rename", "newN", "shop-nginx")]

    def test_the_non_web_tiers_follow_the_swap(self, tmp_path):
        orch = self._swap(tmp_path)
        orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        orch._raw_compose.assert_called_once_with("up", "-d", "--pull", "never", "socketio", "schedule")
        orch._up_workers.assert_called_once()

    def test_an_unhealthy_new_frappe_keeps_the_old_one_serving(self, tmp_path):
        orch = self._swap(tmp_path, frappe_ok=False)
        with pytest.raises(DeployError, match="new frappe replica failed health check; kept old, no swap"):
            orch._rolling_swap(NEW_TAG, OLD_TAG, {"p": b"old"})
        assert ("stop", "oldF") not in self._docker_argv(orch)
        assert ("rm", "oldF") not in self._docker_argv(orch)
        orch._restore_compose.assert_called_once_with({"p": b"old"})
        assert orch._pin_workers.call_args_list[-1].args == (OLD_TAG,)

    def test_an_unhealthy_new_frappe_tears_down_only_the_new_replica(self, tmp_path):
        orch = self._swap(tmp_path, frappe_ok=False)
        with pytest.raises(DeployError):
            orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        assert ("rm", "newF") in self._docker_argv(orch)
        orch._raw_compose.assert_not_called()

    def test_a_missing_new_container_is_the_same_refusal_as_an_unhealthy_one(self, tmp_path):
        orch = self._swap(tmp_path, new_frappe=None)
        with pytest.raises(DeployError, match="new frappe replica failed health check"):
            orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        orch._container_health.assert_not_called()

    def test_an_unhealthy_new_nginx_aborts_after_frappe_came_up(self, tmp_path):
        orch = self._swap(tmp_path, nginx_ok=False)
        with pytest.raises(DeployError, match="new nginx replica failed health check; kept old, no swap"):
            orch._rolling_swap(NEW_TAG, OLD_TAG, {})
        argv = self._docker_argv(orch)
        assert ("rm", "newF") in argv
        assert ("rm", "newN") in argv
        assert ("stop", "oldN") not in argv

    def test_abort_without_a_previous_tag_leaves_the_worker_pin_alone(self, tmp_path):
        orch = self._swap(tmp_path, frappe_ok=False)
        with pytest.raises(DeployError):
            orch._rolling_swap(NEW_TAG, None, {})
        assert [c.args for c in orch._pin_workers.call_args_list] == [(NEW_TAG,)]

    def test_scale_translates_a_failed_compose_into_a_deploy_error(self, tmp_path):
        """A non-zero ``compose`` exit raises DockerException from inside the wrapper -- the
        exit-code fields never come back -- so ``_scale`` must translate, or the swap's abort
        handler never sees a failure it recognises."""
        orch = make_orch(tmp_path)
        orch._raw_compose = MagicMock(side_effect=docker_error("pull access denied"))
        with pytest.raises(DeployError, match=r"compose scale \{'frappe': 2\} failed"):
            orch._scale({"frappe": 2})

    def test_a_failed_scale_aborts_the_swap_and_restores_the_canonical_compose(self, tmp_path):
        """Otherwise the compose is left in the ROLLING render (no ``container_name``): a later
        ``fm start`` creates containers under generated names, ``get_container_names()`` stops
        matching and fm reads the bench as down -- with orphan new replicas left behind."""
        orch = self._swap(tmp_path, scale_error=docker_error("no such image"))
        with pytest.raises(DeployError, match="compose scale"):
            orch._rolling_swap(NEW_TAG, OLD_TAG, {"p": b"old"})
        orch._restore_compose.assert_called_once_with({"p": b"old"})
        assert orch._pin_workers.call_args_list[-1].args == (OLD_TAG,)
        assert ("stop", "oldF") not in self._docker_argv(orch)
        assert ("rm", "newF") in self._docker_argv(orch)


# =============================================================== fetch image


class TestFetchImage:
    def test_fetch_image_translates_a_malformed_tag_into_a_deploy_error(self, tmp_path):
        """``fm switch mybench local/mybench`` (the missing ``:tag`` typo) reaches
        ``transport.fetch_image``, which derives the nginx tag FIRST and raises ``BakeError``
        -- not a ``TransportError``. Untranslated it sails past the CLI's ``except DeployError``
        and the operator gets a Python traceback for a typo."""
        orch = make_orch(tmp_path)
        with pytest.raises(DeployError, match=r"Malformed image tag \(missing ':'\): local/mybench"):
            orch._fetch_image("local/mybench")


# =================================================================== migrate


class TestMigrateStep:
    def _migrator(self, tmp_path, switch=None, result=None, error=None, site_names=None):
        orch = make_orch(tmp_path, switch=switch, site_names=site_names)
        orch.docker.compose.run = MagicMock(
            side_effect=error,
            return_value=result or SimpleNamespace(combined=["ok"]),
        )
        return orch

    def test_migrate_runs_a_one_shot_removed_container_with_the_default_command(self, tmp_path):
        """The default 300s ``migrate_timeout`` wraps the command in the container's own
        ``timeout``, so the argv is ``timeout <budget> <bench> <command>``; before the budget
        was wired the entrypoint was bench itself."""
        orch = self._migrator(tmp_path)
        assert orch._migrate(NEW_TAG) is True
        kwargs = orch.docker.compose.run.call_args.kwargs
        assert kwargs["entrypoint"] == "timeout"
        assert kwargs["command"] == f"300 {BENCH_BIN} --site {SITE} migrate"
        assert kwargs["rm"] is True
        assert kwargs["user"] == "frappe"

    def test_a_configured_migrate_command_replaces_the_default(self, tmp_path):
        orch = self._migrator(
            tmp_path,
            switch=SwitchConfig(migrate_command="--site x migrate --skip-failing", migrate_timeout=0),
        )
        orch._migrate(NEW_TAG)
        kwargs = orch.docker.compose.run.call_args.kwargs
        assert kwargs["entrypoint"] == BENCH_BIN
        assert kwargs["command"] == "--site x migrate --skip-failing"

    def test_the_configured_timeout_is_the_budget_handed_to_the_container(self, tmp_path):
        """``[switch].migrate_timeout`` was advertised (and documented) but never read: a
        migrate that wedged on a lock hung fm forever under an open maintenance window."""
        orch = self._migrator(tmp_path, switch=SwitchConfig(migrate_timeout=42))
        orch._migrate(NEW_TAG)
        kwargs = orch.docker.compose.run.call_args.kwargs
        assert kwargs["entrypoint"] == "timeout"
        assert shlex.split(kwargs["command"])[:2] == ["42", BENCH_BIN]

    def test_timeout_zero_disables_the_budget_and_runs_bench_directly(self, tmp_path):
        orch = self._migrator(tmp_path, switch=SwitchConfig(migrate_timeout=0))
        orch._migrate(NEW_TAG)
        kwargs = orch.docker.compose.run.call_args.kwargs
        assert kwargs["entrypoint"] == BENCH_BIN
        assert kwargs["command"] == f"--site {SITE} migrate"

    def test_a_killed_migrate_names_the_budget_that_killed_it(self, tmp_path):
        """``timeout`` reports the kill as exit 124; anything else stays a DockerException so
        the ordinary migrate-failure text (and its docker output) is untouched."""
        error = DockerException(["docker", "compose", "run"], SubprocessOutput(["waiting"], [], ["waiting"], 124))
        orch = self._migrator(tmp_path, switch=SwitchConfig(migrate_timeout=42), error=error)
        with pytest.raises(DeployError, match=r"migrate_timeout \(42s\)"):
            orch._migrate(NEW_TAG)
        assert "waiting" in orch._migrate_log_host.read_text()

    def test_an_ordinary_migrate_failure_is_not_reported_as_a_timeout(self, tmp_path):
        orch = self._migrator(tmp_path, error=docker_error("patch exploded"))
        with pytest.raises(DockerException):
            orch._migrate(NEW_TAG)

    def test_the_migrate_log_is_persisted_and_exported_to_hook_env(self, tmp_path):
        orch = self._migrator(tmp_path, result=SimpleNamespace(combined=["line one\n", "line two"]))
        orch._migrate(NEW_TAG)
        assert orch._migrate_log_host.read_text() == f"===== {SITE} =====\nline one\nline two"
        assert orch._migrate_log_container == f"/workspace/frappe-bench/logs/{orch._migrate_log_host.name}"
        script = orch._hook_script("echo hi", NEW_TAG)
        assert f"export MIGRATE_LOG_FILE={orch._migrate_log_container}" in script

    def test_a_failed_migrate_still_persists_its_output_and_reraises(self, tmp_path):
        error = docker_error("patch exploded")
        orch = self._migrator(tmp_path, error=error)
        with pytest.raises(DockerException):
            orch._migrate(NEW_TAG)
        assert "patch exploded" in orch._migrate_log_host.read_text()

    def test_every_site_is_migrated_once_in_site_names_order(self, tmp_path):
        """N sites means N schemas, so N migrates, primary first.

        One image swap moves the code under every schema at once, so migrating the primary and
        leaving the rest is exactly "new code against an old schema" for every other site. The
        order is `site_names` order, not any order the sites happen to sort in: the reverse-order
        rollback_db restore unwinds this walk, so the walk has to be the promised one.
        """
        orch = self._migrator(tmp_path, site_names=[SITE, SITE2])
        assert orch._migrate(NEW_TAG) is True
        assert [c.kwargs["command"] for c in orch.docker.compose.run.call_args_list] == [
            f"300 {BENCH_BIN} --site {SITE} migrate",
            f"300 {BENCH_BIN} --site {SITE2} migrate",
        ]

    def test_the_migrate_loop_stops_at_the_first_failing_site(self, tmp_path):
        """The pipeline keeps the OLD image on a migrate failure and `bench migrate` is
        resumable, so re-running the switch after the fix picks up where it stopped. Marching on
        into the remaining sites would spend the whole window on schemas that are about to be
        rolled back anyway."""
        orch = self._migrator(tmp_path, site_names=[SITE, SITE2], error=docker_error("patch exploded"))
        with pytest.raises(DockerException):
            orch._migrate(NEW_TAG)
        commands = [c.kwargs["command"] for c in orch.docker.compose.run.call_args_list]
        assert commands == [f"300 {BENCH_BIN} --site {SITE} migrate"]
        assert SITE2 not in orch._migrate_log_host.read_text()

    def test_a_custom_migrate_command_runs_once_not_once_per_site(self, tmp_path):
        """It replaces the whole command INCLUDING the site selector, so fanning it out would
        run the operator's own command N times against the one site they named in it."""
        orch = self._migrator(
            tmp_path,
            switch=SwitchConfig(migrate_command="--site x migrate --skip-failing", migrate_timeout=0),
            site_names=[SITE, SITE2],
        )
        orch._migrate(NEW_TAG)
        orch.docker.compose.run.assert_called_once()
        assert orch.docker.compose.run.call_args.kwargs["command"] == "--site x migrate --skip-failing"

    def test_one_log_carries_every_site_behind_its_own_separator(self, tmp_path):
        """The hook contract is a single MIGRATE_LOG_FILE, so the sites share one file and the
        separator is what makes it readable."""
        orch = self._migrator(
            tmp_path,
            site_names=[SITE, SITE2],
            result=SimpleNamespace(combined=["done"]),
        )
        orch._migrate(NEW_TAG)
        assert orch._migrate_log_host.read_text() == f"===== {SITE} =====\ndone\n===== {SITE2} =====\ndone"


# ============================================================ release pruning


class TestPruneReleases:
    def _pruner(self, tmp_path, tags, keep_releases=7, current=None, previous=None, backups=None):
        backups = backups or {}
        history = [
            DeployStateEntry(
                tag=t,
                deployed_at=f"t{i}",
                migrate_status="migrated",
                backups={SITE: backups[t]} if backups.get(t) else {},
            )
            for i, t in enumerate(tags)
        ]
        state = DeployState(current_tag=current, previous_tag=previous, history=history)
        orch = make_orch(tmp_path, switch=SwitchConfig(keep_releases=keep_releases), deploy_state=state)
        orch.docker.rmi = MagicMock()
        return orch

    def test_an_empty_history_prunes_nothing(self, tmp_path):
        orch = make_orch(tmp_path, deploy_state=DeployState())
        assert orch.prune_releases() == {"entries": 0, "backups": [], "images": [], "kept": 0}
        orch.config.export_to_toml.assert_not_called()

    def test_keep_defaults_to_the_configured_retention(self, tmp_path):
        orch = self._pruner(tmp_path, ["repo:a", "repo:b", "repo:c"], keep_releases=2)
        summary = orch.prune_releases()
        assert summary["kept"] == 2
        assert summary["entries"] == 1
        assert [e.tag for e in orch.config.deploy_state.history] == ["repo:b", "repo:c"]

    def test_an_explicit_keep_overrides_the_configured_retention(self, tmp_path):
        orch = self._pruner(tmp_path, ["repo:a", "repo:b", "repo:c"], keep_releases=99)
        assert orch.prune_releases(keep=1)["entries"] == 2

    def test_a_colonless_recorded_tag_makes_prune_raise(self, tmp_path):
        """Pinned, not endorsed: deriving the nginx pair refuses a tag with no
        ``:``. ``deploy()`` swallows this into a warning; ``fm prune`` does not."""
        from frappe_manager.site_manager.modules.bake import BakeError

        orch = self._pruner(tmp_path, ["untagged", "repo:b"], keep_releases=1)
        with pytest.raises(BakeError, match="Malformed image tag"):
            orch.prune_releases()

    def test_a_pruned_tag_is_removed_together_with_its_nginx_pair(self, tmp_path):
        orch = self._pruner(tmp_path, ["repo:a", "repo:b"], keep_releases=1)
        summary = orch.prune_releases()
        assert summary["images"] == ["repo:a", "repo-nginx:a"]
        assert [c.args[0] for c in orch.docker.rmi.call_args_list] == ["repo:a", "repo-nginx:a"]

    def test_a_tag_the_bench_can_still_switch_back_to_is_never_removed(self, tmp_path):
        orch = self._pruner(tmp_path, ["repo:a", "repo:b"], keep_releases=1, previous="repo:a")
        summary = orch.prune_releases()
        assert summary["entries"] == 1  # the row goes
        assert summary["images"] == []  # the artifact stays
        orch.docker.rmi.assert_not_called()

    def test_the_seed_image_is_protected_too(self, tmp_path):
        orch = self._pruner(tmp_path, ["repo:a", "repo:b"], keep_releases=1)
        orch.config.seed_image = "repo:a"
        assert orch.prune_releases()["images"] == []

    def test_a_recorded_backup_dir_is_reported_and_deleted(self, tmp_path):
        backup_dir = tmp_path / "bench" / "backups" / "deploy-20240101000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "db.sql").write_text("dump")
        orch = self._pruner(
            tmp_path,
            ["repo:a", "repo:b"],
            keep_releases=1,
            backups={"repo:a": str(backup_dir / "db.sql")},
        )
        summary = orch.prune_releases()
        assert summary["backups"] == [str(backup_dir)]
        assert not backup_dir.exists()

    def test_a_backup_dir_that_is_not_a_deploy_dir_is_left_alone(self, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        (other / "db.sql").write_text("dump")
        orch = self._pruner(
            tmp_path,
            ["repo:a", "repo:b"],
            keep_releases=1,
            backups={"repo:a": str(other / "db.sql")},
        )
        assert orch.prune_releases()["backups"] == []
        assert other.exists()

    def test_dry_run_reports_without_deleting_or_rewriting_state(self, tmp_path):
        backup_dir = tmp_path / "bench" / "backups" / "deploy-20240101000000"
        backup_dir.mkdir(parents=True)
        orch = self._pruner(
            tmp_path,
            ["repo:a", "repo:b"],
            keep_releases=1,
            backups={"repo:a": str(backup_dir / "db.sql")},
        )
        summary = orch.prune_releases(dry_run=True)
        assert summary["entries"] == 1
        assert summary["images"] == ["repo:a", "repo-nginx:a"]
        assert backup_dir.exists()
        orch.docker.rmi.assert_not_called()
        orch.config.export_to_toml.assert_not_called()
        assert len(orch.config.deploy_state.history) == 2

    def test_a_short_history_writes_nothing_back(self, tmp_path):
        orch = self._pruner(tmp_path, ["repo:a"], keep_releases=7)
        assert orch.prune_releases()["entries"] == 0
        orch.config.export_to_toml.assert_not_called()


# ============================================================== health probes


@pytest.mark.usefixtures("no_sleep")
class TestHealthAndRunningProbes:
    def _probe(self, tmp_path, codes):
        orch = make_orch(tmp_path)
        orch.docker.compose.exec = MagicMock(
            side_effect=[c if isinstance(c, Exception) else SimpleNamespace(stdout=[c]) for c in codes],
        )
        return orch

    @pytest.mark.parametrize("code", ["200", "404", "503"])
    def test_server_up_codes_pass_the_gate(self, tmp_path, code):
        assert self._probe(tmp_path, [code])._health_check(retries=1) is True

    def test_a_bad_gateway_keeps_retrying_and_then_fails(self, tmp_path):
        orch = self._probe(tmp_path, ["502", "502", "200"])
        assert orch._health_check(retries=2) is False
        assert orch.docker.compose.exec.call_count == 2

    def test_an_exec_error_is_retried_not_fatal(self, tmp_path):
        orch = self._probe(tmp_path, [docker_error("not up yet"), "200"])
        assert orch._health_check(retries=2) is True

    def test_frappe_running_matches_the_canonical_container_by_name_and_state(self, tmp_path):
        orch = make_orch(tmp_path)
        orch.compose.get_container_names.return_value = {"frappe": "shop-frappe"}
        orch.docker.compose.get_all_services_status.return_value = [
            {"Name": "shop-nginx", "State": "running"},
            {"Name": "shop-frappe", "State": "running"},
        ]
        assert orch._frappe_running() is True

    def test_a_stopped_frappe_is_not_running(self, tmp_path):
        orch = make_orch(tmp_path)
        orch.compose.get_container_names.return_value = {"frappe": "shop-frappe"}
        orch.docker.compose.get_all_services_status.return_value = [
            {"Name": "shop-frappe", "State": "exited"},
        ]
        assert orch._frappe_running() is False

    def test_an_unreachable_daemon_reads_as_not_running(self, tmp_path):
        orch = make_orch(tmp_path)
        orch.docker.compose.get_all_services_status.side_effect = docker_error("daemon gone")
        assert orch._frappe_running() is False


# ===================================================== per-site database config


class TestPerSiteDatabaseResolution:
    """``_external_db`` is answered PER SITE, and ``_db_manager`` follows that answer.

    One bench can serve one site on fm's own global-db container and another on a server fm
    does not own. Resolving the entry once from the PRIMARY makes the external site's TLS
    handling a property of a DIFFERENT site's configuration: the client is handed no CA for a
    server that demands one, and the failure that follows carries no hint saying why.
    """

    def _mixed_bench(self, tmp_path, external_site):
        """A two-site bench where exactly ``external_site`` lives on an external server."""
        orch = make_orch(tmp_path, site_names=[SITE, SITE2])
        entry = SimpleNamespace(host="db.example", port=3306)
        orch.config.database[external_site] = entry
        return orch, entry

    def test_the_secondary_sites_entry_is_read_for_the_secondary_site(self, tmp_path):
        orch, entry = self._mixed_bench(tmp_path, SITE2)
        assert orch._external_db(SITE) is None
        assert orch._external_db(SITE2) is entry

    def test_the_primarys_entry_is_not_handed_to_the_secondary_site(self, tmp_path):
        """The mirror arrangement. Asked about a site on global-db while the PRIMARY is the
        external one, the answer is still that site's own: None."""
        orch, entry = self._mixed_bench(tmp_path, SITE)
        assert orch._external_db(SITE) is entry
        assert orch._external_db(SITE2) is None

    def test_only_the_external_site_is_given_a_mysql_home(self, tmp_path):
        """The observable consequence. MYSQL_HOME is the only way the client learns a CA, so
        it has to name the CA of the site being talked to; global-db gets None, whose
        certificate an external CA would not describe."""
        orch, _ = self._mixed_bench(tmp_path, SITE2)
        with (
            patch(
                "frappe_manager.site_manager.modules.deploy_orchestrator"
                ".DatabaseServerServiceInfo.import_from_bench",
                return_value=SimpleNamespace(name="sitedb"),
            ),
            patch("frappe_manager.site_manager.modules.deploy_orchestrator.MariaDBManager") as mariadb,
        ):
            orch._db_manager(SITE)
            orch._db_manager(SITE2)
        assert [call.kwargs["mysql_home"] for call in mariadb.call_args_list] == [
            None,
            db_tls.site_mysql_home(SITE2),
        ]


# ==================================================================== backup


class TestBackupStep:
    def _backupper(
        self,
        tmp_path,
        running=True,
        db_name="shopdb",
        export_error=None,
        external=False,
        site_names=None,
    ):
        """``db_name`` is one schema name for every site, or a ``{site: name}`` mapping."""
        orch = make_orch(tmp_path, site_names=site_names)
        sites_dir = orch.bench_path / "workspace" / "frappe-bench" / "sites"
        for site in orch.sites:
            (sites_dir / site).mkdir(parents=True, exist_ok=True)
            (sites_dir / site / "site_config.json").write_text("{}")
        (sites_dir / "common_site_config.json").write_text("{}")
        orch._frappe_running = MagicMock(return_value=running)
        manager = MagicMock()
        manager.db_export.side_effect = export_error
        names = db_name if isinstance(db_name, dict) else dict.fromkeys(orch.sites, db_name)
        orch._db_manager = MagicMock(side_effect=lambda site: (manager, names[site]))
        if external:
            orch.config.database[SITE] = SimpleNamespace(host="db.example", port=3306)
        return orch, manager

    def _writes_the_dump(self, orch):
        """A ``db_export`` side effect that really writes the container-side dump file."""

        def _export(_db_name, container_path):
            host = orch.bench_path / "workspace" / Path(container_path).relative_to("/workspace")
            host.write_text("DUMP")

        return _export

    def test_config_snapshots_are_taken_even_when_the_dump_is_skipped(self, tmp_path):
        """These are host-side file copies that never needed the container, so a stopped frappe
        stops the dump and not the snapshot, and it stops it for no site: the per-site copies
        once sat behind the running gate, which lost them for exactly the bench that was down.
        """
        orch, _ = self._backupper(tmp_path, running=False, site_names=[SITE, SITE2])
        target = tmp_path / "out"
        assert orch._backup_all(target) == {}
        assert (target / "common_site_config.json").exists()
        assert (target / f"{SITE}__site_config.json").exists()
        assert (target / f"{SITE2}__site_config.json").exists()

    def test_a_stopped_container_skips_the_dump_with_a_warning(self, tmp_path):
        orch, manager = self._backupper(tmp_path, running=False)
        assert orch._backup_all(tmp_path / "out") == {}
        manager.db_export.assert_not_called()
        assert any("skipping DB backup" in str(c.args) for c in orch.output.warning.call_args_list)

    def test_an_unresolvable_db_name_skips_the_dump(self, tmp_path):
        orch, manager = self._backupper(tmp_path, db_name=None)
        assert orch._backup_all(tmp_path / "out") == {}
        manager.db_export.assert_not_called()

    def test_a_successful_dump_is_moved_into_the_backup_dir(self, tmp_path):
        orch, manager = self._backupper(tmp_path)
        logs = orch.bench_path / "workspace" / "frappe-bench" / "logs"
        manager.db_export.side_effect = self._writes_the_dump(orch)
        target = tmp_path / "out"
        assert orch._backup_all(target) == {SITE: target / "db-shopdb.sql"}
        assert (target / "db-shopdb.sql").read_text() == "DUMP"
        assert not (logs / f"deploy-db-backup-{SITE}.sql").exists()

    def test_every_site_is_dumped_and_the_mapping_is_keyed_by_site(self, tmp_path):
        """The whole point of the change: N sites means N schemas, so N dumps.

        A switch that backed up only the primary left every other site with new code over an
        old schema and nothing to roll back to. The dump paths are keyed by the site's own
        schema name, so two sites can never race through one path.
        """
        orch, manager = self._backupper(
            tmp_path,
            site_names=[SITE, SITE2],
            db_name={SITE: "shopdb", SITE2: "warehousedb"},
        )
        manager.db_export.side_effect = self._writes_the_dump(orch)
        target = tmp_path / "out"
        assert orch._backup_all(target) == {
            SITE: target / "db-shopdb.sql",
            SITE2: target / "db-warehousedb.sql",
        }
        assert [c.args[0] for c in manager.db_export.call_args_list] == ["shopdb", "warehousedb"]
        assert [c.args[0] for c in orch._db_manager.call_args_list] == [SITE, SITE2]

    def test_one_site_failing_its_dump_leaves_only_that_site_out(self, tmp_path):
        """Absent from the mapping, never mapped to None: the caller counts the mapping against
        the site list to decide whether the rollback set is complete."""
        orch, manager = self._backupper(
            tmp_path,
            site_names=[SITE, SITE2],
            db_name={SITE: "shopdb", SITE2: None},
        )
        manager.db_export.side_effect = self._writes_the_dump(orch)
        target = tmp_path / "out"
        assert orch._backup_all(target) == {SITE: target / "db-shopdb.sql"}

    def test_a_dump_that_never_appeared_yields_no_backup_path(self, tmp_path):
        orch, _ = self._backupper(tmp_path)
        assert orch._backup_all(tmp_path / "out") == {}

    def test_an_export_failure_continues_the_deploy_without_a_dump(self, tmp_path):
        orch, _ = self._backupper(tmp_path, export_error=docker_error("access denied"))
        assert orch._backup_all(tmp_path / "out") == {}
        assert any("DB export failed" in str(c.args) for c in orch.output.warning.call_args_list)

    def test_an_external_export_failure_adds_the_tls_hint(self, tmp_path):
        orch, _ = self._backupper(tmp_path, export_error=docker_error("access denied"), external=True)
        orch._backup_all(tmp_path / "out")
        warned = " ".join(str(c.args) for c in orch.output.warning.call_args_list)
        assert "ca-bundle.pem" in warned
