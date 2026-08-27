"""The deploy's two data-loss guards: the insurance backup, and the restore confirmation.

**A failed backup is not a warning when a rollback was promised.** `_backup` gives up in four
different ways (frappe container stopped, DB name unresolvable, `mariadb-dump` refused, dump file
never appeared) and every one of them returns `None` after a single warning inside a spinner. The
migrate-failure path is guarded `if self.switch_config.rollback_db and db_dump:`, so a `None` dump
turns the whole safety net into a no-op silently: `bench migrate` runs anyway, blows up, and there
is nothing to restore. A bench configured `backup_db = true, rollback_db = true` asked for exactly
two things and got neither. So when the dump was EXPLICITLY requested and `rollback_db` is on, the
deploy aborts at the backup step, where the abort is still free: maintenance is dropped, the
workers resume, the compose is reverted and the old stack keeps serving. `backup_db = 'auto'` is a
probe, not a promise, and is deliberately left as warn-and-continue.

**A restore replaces the current database, on both kinds of server.** `db_import(force=True)` +
a Frappe dump's per-table `DROP TABLE IF EXISTS` is the most destructive thing fm does. The typed
schema-name confirmation used to be reached only for a `[database]` entry, so the schema fm owns
in global-db was overwritten with no question at all, losing exactly as much site data. Both are
confirmed now. The two paths that reach a restore are NOT the same, though, and the distinction is
the contract:

* the operator's `--restore-db` puts an OLD dump on top of everything written since. It refuses
  when it cannot ask, and `--yes` is the only unattended way through.
* fm's own `rollback_db` insurance restores the dump fm took minutes ago from this same database.
  A human who cannot be reached must not stand between a failed migrate and its recovery, so that
  path still warns and proceeds.

Nothing here touches docker: the DB manager is a mock and the assertions read what it was handed.
"""

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import BenchRuntime, SwitchConfig
from frappe_manager.site_manager.modules.deploy_orchestrator import (
    DeployError,
    DeployOrchestrator,
    RestoreNotConfirmed,
)

SITE = "shop.localhost"
NEW_TAG = "reg.example/shop:v2"
DB = "shopdb"

#: Everything ``deploy()`` delegates to, EXCEPT ``_backup``: the backup step is the
#: subject here, so it is stubbed per-test (or left real).
SPIED = (
    "_fetch_image",
    "_snapshot_compose",
    "_restore_compose",
    "_unwind_maintenance",
    "_pin_workers",
    "_probe_migrate_needed",
    "_set_maintenance",
    "drain_workers",
    "resume_workers",
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


class FakeConfig:
    def __init__(self, root_path, switch):
        self.runtime = BenchRuntime.image
        self.image = NEW_TAG
        self.switch = switch
        self.workers = None
        self.root_path = str(root_path)
        self.deploy_state = None
        self.db_name = DB
        self.apps_list = []
        self.seed_image = None
        self.base_image = None
        self.registry = None
        self.database = {}
        self.export_to_toml = MagicMock()

    def get_database_config(self, site):
        return self.database.get(site)


def _orch(tmp_path, switch=None, external=False):
    config = FakeConfig(tmp_path, switch if switch is not None else SwitchConfig())
    if external:
        config.database[SITE] = SimpleNamespace(host="db.example", port=3306)
    bench_path = tmp_path / "bench"
    (bench_path / "workspace" / "frappe-bench" / "logs").mkdir(parents=True, exist_ok=True)
    bench = SimpleNamespace(
        name=SITE,
        path=bench_path,
        bench_config=config,
        docker_client=MagicMock(),
        compose_file_manager=MagicMock(),
        docker_ops=MagicMock(),
        workers=MagicMock(),
        set_common_bench_config=MagicMock(),
        set_bench_site_config=MagicMock(),
    )
    return DeployOrchestrator(bench, output_handler=MagicMock())


def _rig(tmp_path, switch=None, running=True, backup=None, external=False):
    """A deploy whose collaborators are spies. ``backup`` replaces ``_backup``; pass
    ``None`` to leave the real one in place."""
    orch = _orch(tmp_path, switch=switch, external=external)
    for name in SPIED:
        setattr(orch, name, MagicMock(name=name))
    orch._snapshot_compose.return_value = {"snap": b"x"}
    orch.drain_workers.return_value = True
    orch._health_check.return_value = True
    orch._probe_migrate_needed.return_value = True
    orch._frappe_running = MagicMock(return_value=running)
    if backup is not None:
        orch._backup = backup
    return orch


def _docker_error(msg="access denied"):
    return DockerException(["docker", "compose", "exec"], SubprocessOutput([msg], [msg], [msg], 1))


def _warnings(orch):
    return " ".join(str(c.args) for c in orch.output.warning.call_args_list)


# ============================================ the backup the operator was promised


PROMISED = SwitchConfig(migrate=True, backup_db=True, rollback_db=True)


class TestFailedBackupAborts:
    def test_a_failed_backup_aborts_before_the_migrate(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, backup=MagicMock(return_value=None))
        with pytest.raises(DeployError):
            orch.deploy(NEW_TAG)
        orch._migrate.assert_not_called()

    def test_a_failed_backup_aborts_before_the_swap(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, backup=MagicMock(return_value=None))
        with pytest.raises(DeployError):
            orch.deploy(NEW_TAG)
        orch._rolling_swap.assert_not_called()
        orch.docker.compose.up.assert_not_called()
        orch._record.assert_not_called()

    def test_the_abort_leaves_the_old_stack_serving(self, tmp_path):
        """Nothing irreversible has happened yet, so the abort must undo the window it
        opened: compose reverted, page dropped, workers resumed."""
        orch = _rig(tmp_path, switch=PROMISED, backup=MagicMock(return_value=None))
        with pytest.raises(DeployError):
            orch.deploy(NEW_TAG)
        orch._restore_compose.assert_called_once()
        orch._unwind_maintenance.assert_called_once()

    def test_the_abort_message_names_the_setting_that_would_relax_it(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, backup=MagicMock(return_value=None))
        with pytest.raises(DeployError) as exc:
            orch.deploy(NEW_TAG)
        assert "rollback_db" in str(exc.value)
        assert "nothing to restore" in str(exc.value)

    def test_a_successful_backup_deploys_normally(self, tmp_path):
        dump = tmp_path / "db.sql"
        orch = _rig(tmp_path, switch=PROMISED, backup=MagicMock(return_value=dump))
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)

    def test_rollback_db_off_still_tolerates_a_failed_backup(self, tmp_path):
        """Without rollback_db nothing was promised about restoring, so the dump is a
        convenience and its loss stays a warning."""
        orch = _rig(
            tmp_path,
            switch=SwitchConfig(migrate=True, backup_db=True, rollback_db=False),
            backup=MagicMock(return_value=None),
        )
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)

    def test_backup_db_auto_is_a_probe_not_a_promise(self, tmp_path):
        """'auto' means "dump if this looks like a schema change"; it is not the operator
        asserting a dump exists, so a failure there is not an abort."""
        orch = _rig(
            tmp_path,
            switch=SwitchConfig(migrate=True, backup_db="auto", rollback_db=True),
            backup=MagicMock(return_value=None),
        )
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)

    def test_the_migrate_failure_path_can_no_longer_reach_a_missing_dump(self, tmp_path):
        """The bug this guard closes: migrate fails, rollback_db is on, and `and db_dump`
        quietly skips the restore. With the guard the deploy never gets that far."""
        orch = _rig(
            tmp_path,
            switch=PROMISED,
            backup=MagicMock(return_value=None),
        )
        orch._migrate.side_effect = _docker_error("patch blew up")
        with pytest.raises(DeployError, match="nothing to restore"):
            orch.deploy(NEW_TAG)
        orch._restore_db.assert_not_called()
        orch._notify_after_migrate.assert_not_called()


class TestEveryWayTheBackupGivesUp:
    """The real ``_backup``: all four give-up arms abort identically."""

    def _real_backup_rig(self, tmp_path, *, running=True, db_name=DB, export=None, produce_dump=False):
        orch = _rig(tmp_path, switch=PROMISED, running=running, external=False)
        sites = orch.bench_path / "workspace" / "frappe-bench" / "sites" / SITE
        sites.mkdir(parents=True, exist_ok=True)
        (sites / "site_config.json").write_text("{}")
        manager = MagicMock()
        manager.database_server_info = SimpleNamespace(host="global-db", port=3306)
        logs = orch.bench_path / "workspace" / "frappe-bench" / "logs"
        if export is not None:
            manager.db_export.side_effect = export
        elif produce_dump:
            manager.db_export.side_effect = lambda *_a: (logs / "deploy-db-backup.sql").write_text("DUMP")
        orch._db_manager = MagicMock(return_value=(manager, db_name))
        return orch

    @pytest.mark.parametrize(
        ("label", "kwargs"),
        [
            ("frappe container stopped", {"running": False}),
            ("db name unresolvable", {"db_name": None}),
            ("mariadb-dump refused", {"export": _docker_error()}),
            ("dump file never appeared", {}),
        ],
    )
    def test_every_give_up_arm_aborts_the_deploy(self, tmp_path, label, kwargs):
        orch = self._real_backup_rig(tmp_path, **kwargs)
        with pytest.raises(DeployError, match="nothing to restore"):
            orch.deploy(NEW_TAG)
        assert orch._migrate.call_count == 0, label

    def test_a_real_dump_that_lands_deploys(self, tmp_path):
        orch = self._real_backup_rig(tmp_path, produce_dump=True)
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)
        recorded = orch._record.call_args.kwargs["backup"]
        assert recorded.name == f"db-{DB}.sql"


# ================================================= the restore confirmation


class TestRequestedRestoreRefuses:
    def _restorer(self, tmp_path, *, external=False, interactive=True, answer=DB):
        orch = _orch(tmp_path, external=external)
        manager = MagicMock()
        manager.database_server_info = SimpleNamespace(host="db.example", port=3306)
        manager.db_run_query.return_value = SimpleNamespace(stdout=["1\t7"])
        orch._db_manager = MagicMock(return_value=(manager, DB))
        orch.output.is_interactive.return_value = interactive
        orch.output.prompt_ask.return_value = answer
        dump = tmp_path / "dump.sql"
        dump.write_text("-- dump")
        return orch, manager, dump

    @pytest.mark.parametrize("external", [False, True])
    def test_a_requested_restore_refuses_when_it_cannot_ask(self, tmp_path, external):
        """The global-db half is the new one: fm owning the container never made the site
        data less valuable."""
        orch, manager, dump = self._restorer(tmp_path, external=external, interactive=False)
        with pytest.raises(RestoreNotConfirmed, match="Nothing was imported"):
            orch._restore_db(dump, requested=True)
        manager.db_import.assert_not_called()

    @pytest.mark.parametrize("external", [False, True])
    def test_the_refusal_names_both_ways_through(self, tmp_path, external):
        orch, _manager, dump = self._restorer(tmp_path, external=external, interactive=False)
        with pytest.raises(RestoreNotConfirmed) as exc:
            orch._restore_db(dump, requested=True)
        assert "--non-interactive" in str(exc.value)
        assert "--yes" in str(exc.value)

    @pytest.mark.parametrize("external", [False, True])
    def test_yes_is_the_unattended_way_through(self, tmp_path, external):
        orch, manager, dump = self._restorer(tmp_path, external=external, interactive=False)
        orch._restore_db(dump, requested=True, confirmed=True)
        manager.db_import.assert_called_once_with(DB, dump, force=True)

    def test_yes_asks_nothing_even_on_a_terminal(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path)
        orch._restore_db(dump, requested=True, confirmed=True)
        orch.output.prompt_ask.assert_not_called()
        manager.db_run_query.assert_not_called()
        manager.db_import.assert_called_once_with(DB, dump, force=True)

    @pytest.mark.parametrize("external", [False, True])
    def test_a_wrong_answer_refuses_on_either_server(self, tmp_path, external):
        orch, manager, dump = self._restorer(tmp_path, external=external, answer="shopdbb")
        with pytest.raises(RestoreNotConfirmed):
            orch._restore_db(dump, requested=True)
        manager.db_import.assert_not_called()

    def test_the_global_db_prompt_says_whose_database_it_is(self, tmp_path):
        orch, _manager, dump = self._restorer(tmp_path, external=False)
        orch._restore_db(dump, requested=True)
        assert "fm's own global-db container" in _warnings(orch)

    def test_the_external_prompt_still_says_fm_does_not_own_it(self, tmp_path):
        orch, _manager, dump = self._restorer(tmp_path, external=True)
        orch._restore_db(dump, requested=True)
        assert "a database fm does not own" in _warnings(orch)

    def test_the_global_db_table_count_is_read_from_the_server(self, tmp_path):
        """The number in the question has to be current, or it is false reassurance."""
        orch, manager, dump = self._restorer(tmp_path, external=False)
        orch._restore_db(dump, requested=True)
        manager.db_run_query.assert_called_once()
        assert "it holds 7 tables right now" in _warnings(orch)


class TestInsuranceRestoreStillRuns:
    """``rollback_db`` is fm restoring its own minutes-old dump of this same database.
    Requiring a human there would turn a failed migrate into an unrecovered one."""

    def _restorer(self, tmp_path, *, external=False):
        orch = _orch(tmp_path, external=external)
        manager = MagicMock()
        manager.database_server_info = SimpleNamespace(host="db.example", port=3306)
        orch._db_manager = MagicMock(return_value=(manager, DB))
        orch.output.is_interactive.return_value = False
        dump = tmp_path / "dump.sql"
        dump.write_text("-- dump")
        return orch, manager, dump

    @pytest.mark.parametrize("external", [False, True])
    def test_an_unattended_insurance_restore_imports_with_a_warning(self, tmp_path, external):
        orch, manager, dump = self._restorer(tmp_path, external=external)
        orch._restore_db(dump)
        manager.db_import.assert_called_once_with(DB, dump, force=True)
        assert "unconfirmed" in _warnings(orch)

    def test_the_migrate_failure_restore_is_the_insurance_path(self, tmp_path):
        """It must NOT be marked requested, or an unattended deploy would lose its
        rollback exactly when it needs it."""
        dump = tmp_path / "db.sql"
        orch = _rig(tmp_path, switch=PROMISED, backup=MagicMock(return_value=dump))
        orch._migrate.side_effect = _docker_error("patch blew up")
        with pytest.raises(DeployError, match="Migration failed"):
            orch.deploy(NEW_TAG)
        orch._restore_db.assert_called_once_with(dump)


class TestRequestedRestoreThroughDeploy:
    def test_deploy_marks_the_operators_dump_as_requested(self, tmp_path):
        dump = tmp_path / "old.sql"
        orch = _rig(tmp_path, switch=SwitchConfig(migrate=False), backup=MagicMock(return_value=None))
        orch.deploy(NEW_TAG, restore_db_dump=dump)
        orch._restore_db.assert_called_once_with(dump, requested=True, confirmed=False)

    def test_the_yes_bypass_reaches_the_restore(self, tmp_path):
        dump = tmp_path / "old.sql"
        orch = _rig(tmp_path, switch=SwitchConfig(migrate=False), backup=MagicMock(return_value=None))
        orch.deploy(NEW_TAG, restore_db_dump=dump, restore_confirmed=True)
        orch._restore_db.assert_called_once_with(dump, requested=True, confirmed=True)

    def test_a_declined_restore_aborts_the_deploy_before_the_swap(self, tmp_path):
        """Unlike the insurance restore, this one is not caught: refusing the import means
        refusing the deploy, and the old stack keeps serving."""
        dump = tmp_path / "old.sql"
        orch = _rig(tmp_path, switch=SwitchConfig(migrate=False), backup=MagicMock(return_value=None))
        orch._restore_db.side_effect = RestoreNotConfirmed("not typed")
        with pytest.raises(RestoreNotConfirmed):
            orch.deploy(NEW_TAG, restore_db_dump=dump)
        orch._rolling_swap.assert_not_called()
        orch.docker.compose.up.assert_not_called()
        orch._restore_compose.assert_called_once()
        orch._unwind_maintenance.assert_called_once()


# ============================================================== the CLI surface


class TestSwitchFlagSurface:
    def test_restore_db_help_admits_the_current_database_is_replaced(self):
        """It used to say only "also restore the DB dump", which reads additive. The dump
        opens with DROP TABLE IF EXISTS per table."""
        from frappe_manager.commands.deploy import switch

        help_text = inspect.signature(switch).parameters["restore_db"].annotation.__metadata__[0].help
        assert "REPLACES the current database" in help_text
        assert "lost" in help_text

    def test_switch_threads_the_bypass_into_the_orchestrator(self):
        """`--yes` exists only to answer the restore question up front; if it did not reach
        `deploy()` the unattended rollback would be impossible."""
        from frappe_manager.commands.deploy import switch

        params = inspect.signature(switch).parameters
        assert "yes" in params
        assert set(params["yes"].annotation.__metadata__[0].param_decls) <= {"--yes", "-y"}
        assert "restore_confirmed" in inspect.signature(DeployOrchestrator.deploy).parameters


def test_backup_returns_a_path_or_none_and_the_caller_decides(tmp_path):
    """``_backup`` deliberately keeps reporting failure as None rather than raising: the
    'auto' probe and the rollback path need different verdicts from the same function, so
    the promise-keeping decision belongs to ``deploy()``."""
    orch = _orch(tmp_path)
    orch._frappe_running = MagicMock(return_value=False)
    assert orch._backup(tmp_path / "out") is None
    assert inspect.signature(orch._backup).return_annotation in (Path | None, "Path | None")
