"""The deploy's two data-loss guards: the insurance backup, and the restore confirmation.

**A failed backup is not a warning when a rollback was promised.** `_backup_site` gives up in four
different ways (frappe container stopped, DB name unresolvable, `mariadb-dump` refused, dump file
never appeared) and every one of them returns `None` after a single warning inside a spinner, which
leaves that site OUT of the `{site: dump}` mapping `_backup_all` hands back. The migrate-failure
path is guarded `if self.switch_config.rollback_db and db_dumps:`, so a site with no dump turns the
whole safety net into a no-op silently for that site: `bench migrate` runs anyway, blows up, and
there is nothing to restore. A bench configured `backup_db = true, rollback_db = true` asked for
exactly two things and got neither. So when the dump was EXPLICITLY requested and `rollback_db` is
on, the deploy aborts at the backup step, where the abort is still free: maintenance is dropped, the
workers resume, the compose is reverted and the old stack keeps serving.
`backup_db = 'auto'` is a probe, not a promise, and is deliberately left as warn-and-continue.

**Every site, or none.** A bench serves N sites and each one is its own schema on its own database,
so a switch takes N dumps and the guard counts them against the site list. A partial set is a failed
backup even though something was dumped: restoring one site of two on a failed migrate would leave
the bench at two points in time, which is worse than the deploy that failed. So ANY site missing
from the mapping aborts, and the message names the sites that are missing rather than reporting a
nameless backup failure on a bench where most of the dumps landed.

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
  path still warns and proceeds, per site, in reverse site order, and one site's refusal does not
  strand the sites behind it.

Nothing here touches docker: the DB manager is a mock and the assertions read what it was handed.
"""

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

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
OTHER = "books.localhost"
THIRD = "cafe.localhost"
NEW_TAG = "reg.example/shop:v2"
DB = "shopdb"
OTHER_DB = "booksdb"
THIRD_DB = "cafedb"
#: One schema per site, so one dump file name per site: a shared name would race two sites
#: through one path and file one site's rows under another site's dump.
DB_NAMES = {SITE: DB, OTHER: OTHER_DB, THIRD: THIRD_DB}

#: Everything ``deploy()`` delegates to, EXCEPT ``_backup_all``: the backup step is the
#: subject here, so it is stubbed per-test (or left real).
SPIED = (
    "_fetch_image",
    "_snapshot_compose",
    "_restore_compose",
    "_unwind_maintenance",
    "_pin_workers",
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
    def __init__(self, root_path, switch, sites):
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
        # Every site the bench serves, primary first. This is the list every schema-grade
        # step of the deploy walks, so a fake that carries only the primary would let a
        # one-site-only pipeline pass.
        self.site_names = list(sites)
        self.export_to_toml = MagicMock()

    def get_database_config(self, site):
        return self.database.get(site)


def _orch(tmp_path, switch=None, external=False, sites=(SITE,)):
    config = FakeConfig(tmp_path, switch if switch is not None else SwitchConfig(), sites)
    if external:
        config.database[SITE] = SimpleNamespace(host="db.example", port=3306)
    bench_path = tmp_path / "bench"
    (bench_path / "workspace" / "frappe-bench" / "logs").mkdir(parents=True, exist_ok=True)
    bench = SimpleNamespace(
        # bench, site and domain are one string today, and this stand-in must carry all three
        # because the orchestrator correctly asks for the site where it means the site.
        name=SITE,
        site_name=SITE,
        primary_domain=SITE,
        domains=list(sites),
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
    return DeployOrchestrator(bench, output_handler=MagicMock())


def _rig(tmp_path, switch=None, running=True, backups=None, external=False, sites=(SITE,)):
    """A deploy whose collaborators are spies. ``backups`` replaces ``_backup_all``; pass
    ``None`` to leave the real one in place."""
    orch = _orch(tmp_path, switch=switch, external=external, sites=sites)
    for name in SPIED:
        setattr(orch, name, MagicMock(name=name))
    orch._snapshot_compose.return_value = {"snap": b"x"}
    orch.drain_workers.return_value = True
    orch._health_check.return_value = True
    orch._frappe_running = MagicMock(return_value=running)
    if backups is not None:
        orch._backup_all = backups
    return orch


def _docker_error(msg="access denied"):
    return DockerException(["docker", "compose", "exec"], SubprocessOutput([msg], [msg], [msg], 1))


def _warnings(orch):
    return " ".join(str(c.args) for c in orch.output.warning.call_args_list)


def _dumped(orch, *sites):
    """``_backup_all`` stub reporting a dump for exactly ``sites`` (in that order)."""
    return MagicMock(return_value={site: orch.bench_path / f"db-{DB_NAMES[site]}.sql" for site in sites})


def _real_backup_rig(tmp_path, *, sites=(SITE,), running=True, db_names=None, exports=None, dumps=()):
    """A rig running the REAL ``_backup_all``/``_backup_site``: only ``_db_manager`` is a mock.

    ``dumps`` names the sites whose ``db_export`` actually writes the container-side file,
    ``exports`` maps a site to the exception its ``db_export`` raises, and ``db_names`` overrides
    the resolved schema name (``None`` = unresolvable).
    """
    orch = _rig(tmp_path, switch=PROMISED, running=running, sites=sites)
    bench_root = orch.bench_path / "workspace" / "frappe-bench"
    logs = bench_root / "logs"
    names = {site: DB_NAMES[site] for site in sites} | dict(db_names or {})
    managers = {}
    for site in sites:
        site_dir = bench_root / "sites" / site
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "site_config.json").write_text("{}")
        manager = MagicMock()
        manager.database_server_info = SimpleNamespace(host="global-db", port=3306)
        if site in (exports or {}):
            manager.db_export.side_effect = exports[site]
        elif site in dumps:
            # db_export writes inside the container; the host path under logs/ is the same file.
            dump_file = logs / f"deploy-db-backup-{site}.sql"
            manager.db_export.side_effect = lambda *_a, _f=dump_file: _f.write_text("DUMP")
        managers[site] = manager
    orch._db_manager = MagicMock(side_effect=lambda site: (managers[site], names[site]))
    return orch, managers


# ============================================ the backup the operator was promised


PROMISED = SwitchConfig(migrate=True, backup_db=True, rollback_db=True)


class TestFailedBackupAborts:
    def test_a_failed_backup_aborts_before_the_migrate(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, backups=MagicMock(return_value={}))
        with pytest.raises(DeployError):
            orch.deploy(NEW_TAG)
        orch._migrate.assert_not_called()

    def test_a_failed_backup_aborts_before_the_swap(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, backups=MagicMock(return_value={}))
        with pytest.raises(DeployError):
            orch.deploy(NEW_TAG)
        orch._rolling_swap.assert_not_called()
        orch.docker.compose.up.assert_not_called()
        orch._record.assert_not_called()

    def test_the_abort_leaves_the_old_stack_serving(self, tmp_path):
        """Nothing irreversible has happened yet, so the abort must undo the window it
        opened: compose reverted, page dropped, workers resumed."""
        orch = _rig(tmp_path, switch=PROMISED, backups=MagicMock(return_value={}))
        with pytest.raises(DeployError):
            orch.deploy(NEW_TAG)
        orch._restore_compose.assert_called_once()
        orch._unwind_maintenance.assert_called_once()

    def test_the_abort_message_names_the_setting_that_would_relax_it(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, backups=MagicMock(return_value={}))
        with pytest.raises(DeployError) as exc:
            orch.deploy(NEW_TAG)
        assert "rollback_db" in str(exc.value)
        assert "nothing to restore" in str(exc.value)

    def test_a_successful_backup_deploys_normally(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, backups=MagicMock(return_value={SITE: tmp_path / "db.sql"}))
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)

    def test_rollback_db_off_still_tolerates_a_failed_backup(self, tmp_path):
        """Without rollback_db nothing was promised about restoring, so the dump is a
        convenience and its loss stays a warning."""
        orch = _rig(
            tmp_path,
            switch=SwitchConfig(migrate=True, backup_db=True, rollback_db=False),
            backups=MagicMock(return_value={}),
        )
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)

    def test_backup_db_auto_is_a_probe_not_a_promise(self, tmp_path):
        """'auto' means "dump if this looks like a schema change"; it is not the operator
        asserting a dump exists, so a failure there is not an abort. Only `migrate`'s auto
        was removed; `backup_db = 'auto'` still means exactly this."""
        orch = _rig(
            tmp_path,
            switch=SwitchConfig(migrate=True, backup_db="auto", rollback_db=True),
            backups=MagicMock(return_value={}),
        )
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)

    def test_the_migrate_failure_path_can_no_longer_reach_a_missing_dump(self, tmp_path):
        """The bug this guard closes: migrate fails, rollback_db is on, and `and db_dumps`
        quietly skips the restore. With the guard the deploy never gets that far."""
        orch = _rig(
            tmp_path,
            switch=PROMISED,
            backups=MagicMock(return_value={}),
        )
        orch._migrate.side_effect = _docker_error("patch blew up")
        with pytest.raises(DeployError, match="nothing to restore"):
            orch.deploy(NEW_TAG)
        orch._restore_db.assert_not_called()
        orch._notify_after_migrate.assert_not_called()


class TestAPartialDumpSetIsAFailedBackup:
    """N sites means N schemas, so the promise is N dumps and not "a dump".

    A mapping that is merely non-empty is the exact shape of the bug: the sites that DID dump
    make the backup step look like it worked, and the one that did not is discovered only when
    the migrate has already failed and there is nothing to put back.
    """

    def test_two_dumps_on_a_two_site_bench_deploys(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, sites=(SITE, OTHER))
        orch._backup_all = _dumped(orch, SITE, OTHER)
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)
        assert set(orch._record.call_args.kwargs["backups"]) == {SITE, OTHER}

    def test_one_missing_dump_aborts_even_though_the_other_landed(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, sites=(SITE, OTHER))
        orch._backup_all = _dumped(orch, SITE)
        with pytest.raises(DeployError, match="nothing to restore"):
            orch.deploy(NEW_TAG)
        orch._migrate.assert_not_called()
        orch._rolling_swap.assert_not_called()
        orch.docker.compose.up.assert_not_called()
        orch._record.assert_not_called()

    def test_the_missing_dump_is_the_primarys_too(self, tmp_path):
        """The guard counts the site LIST, not "more than zero" and not "the primary": a
        secondary that dumped does not cover a primary that did not."""
        orch = _rig(tmp_path, switch=PROMISED, sites=(SITE, OTHER))
        orch._backup_all = _dumped(orch, OTHER)
        with pytest.raises(DeployError, match="nothing to restore"):
            orch.deploy(NEW_TAG)
        orch._migrate.assert_not_called()

    def test_the_partial_abort_leaves_the_old_stack_serving(self, tmp_path):
        orch = _rig(tmp_path, switch=PROMISED, sites=(SITE, OTHER))
        orch._backup_all = _dumped(orch, SITE)
        with pytest.raises(DeployError):
            orch.deploy(NEW_TAG)
        orch._restore_compose.assert_called_once()
        orch._unwind_maintenance.assert_called_once()

    def test_the_abort_message_names_the_sites_whose_dumps_are_missing(self, tmp_path):
        """A bare "DB backup failed" on a three-site bench is not actionable: the operator has to
        know WHICH schema has no dump to know what to fix, and naming the ones that landed
        would send them after the wrong site."""
        orch = _rig(tmp_path, switch=PROMISED, sites=(SITE, OTHER, THIRD))
        orch._backup_all = _dumped(orch, SITE)
        with pytest.raises(DeployError) as exc:
            orch.deploy(NEW_TAG)
        assert OTHER in str(exc.value)
        assert THIRD in str(exc.value)
        assert SITE not in str(exc.value)

    def test_rollback_db_off_tolerates_a_partial_dump_set(self, tmp_path):
        """The insurance is what makes a gap fatal. Without rollback_db no restore was
        promised for any site, so a site that could not dump stays a warning."""
        orch = _rig(
            tmp_path,
            switch=SwitchConfig(migrate=True, backup_db=True, rollback_db=False),
            sites=(SITE, OTHER),
        )
        orch._backup_all = _dumped(orch, SITE)
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)

    def test_backup_db_auto_tolerates_a_partial_dump_set(self, tmp_path):
        orch = _rig(
            tmp_path,
            switch=SwitchConfig(migrate=True, backup_db="auto", rollback_db=True),
            sites=(SITE, OTHER),
        )
        orch._backup_all = _dumped(orch, SITE)
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)


class TestEveryWayTheBackupGivesUp:
    """The real ``_backup_all``: all four give-up arms abort identically."""

    @pytest.mark.parametrize(
        ("label", "kwargs"),
        [
            ("frappe container stopped", {"running": False}),
            ("db name unresolvable", {"db_names": {SITE: None}}),
            ("mariadb-dump refused", {"exports": {SITE: _docker_error()}}),
            ("dump file never appeared", {}),
        ],
    )
    def test_every_give_up_arm_aborts_the_deploy(self, tmp_path, label, kwargs):
        orch, _managers = _real_backup_rig(tmp_path, **kwargs)
        with pytest.raises(DeployError, match="nothing to restore"):
            orch.deploy(NEW_TAG)
        assert orch._migrate.call_count == 0, label

    def test_a_real_dump_that_lands_deploys(self, tmp_path):
        orch, _managers = _real_backup_rig(tmp_path, dumps=(SITE,))
        orch.deploy(NEW_TAG)
        orch._migrate.assert_called_once_with(NEW_TAG)
        recorded = orch._record.call_args.kwargs["backups"]
        assert {site: path.name for site, path in recorded.items()} == {SITE: f"db-{DB}.sql"}


class TestTheRealBackupCoversEverySite:
    """The contract, through the real backup step: N sites in, N dumps out."""

    def test_a_two_site_bench_dumps_both_schemas(self, tmp_path):
        orch, managers = _real_backup_rig(tmp_path, sites=(SITE, OTHER), dumps=(SITE, OTHER))
        orch.deploy(NEW_TAG)
        # Each site's own schema, exported through its own manager, into its own file.
        managers[SITE].db_export.assert_called_once()
        managers[OTHER].db_export.assert_called_once()
        assert managers[SITE].db_export.call_args.args[0] == DB
        assert managers[OTHER].db_export.call_args.args[0] == OTHER_DB
        recorded = orch._record.call_args.kwargs["backups"]
        assert {site: path.name for site, path in recorded.items()} == {
            SITE: f"db-{DB}.sql",
            OTHER: f"db-{OTHER_DB}.sql",
        }
        assert all(path.exists() for path in recorded.values())

    @pytest.mark.parametrize(
        ("label", "kwargs"),
        [
            ("db name unresolvable", {"db_names": {OTHER: None}}),
            ("mariadb-dump refused", {"exports": {OTHER: _docker_error()}}),
            ("dump file never appeared", {}),
        ],
    )
    def test_a_secondary_site_that_cannot_dump_aborts_the_whole_deploy(self, tmp_path, label, kwargs):
        orch, _managers = _real_backup_rig(tmp_path, sites=(SITE, OTHER), dumps=(SITE,), **kwargs)
        with pytest.raises(DeployError) as exc:
            orch.deploy(NEW_TAG)
        assert OTHER in str(exc.value), label
        assert orch._migrate.call_count == 0, label


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
            orch._restore_db(SITE, dump, requested=True)
        manager.db_import.assert_not_called()

    @pytest.mark.parametrize("external", [False, True])
    def test_the_refusal_names_both_ways_through(self, tmp_path, external):
        orch, _manager, dump = self._restorer(tmp_path, external=external, interactive=False)
        with pytest.raises(RestoreNotConfirmed) as exc:
            orch._restore_db(SITE, dump, requested=True)
        assert "--non-interactive" in str(exc.value)
        assert "--yes" in str(exc.value)

    @pytest.mark.parametrize("external", [False, True])
    def test_yes_is_the_unattended_way_through(self, tmp_path, external):
        orch, manager, dump = self._restorer(tmp_path, external=external, interactive=False)
        orch._restore_db(SITE, dump, requested=True, confirmed=True)
        manager.db_import.assert_called_once_with(DB, dump, force=True)

    def test_yes_asks_nothing_even_on_a_terminal(self, tmp_path):
        orch, manager, dump = self._restorer(tmp_path)
        orch._restore_db(SITE, dump, requested=True, confirmed=True)
        orch.output.prompt_ask.assert_not_called()
        manager.db_run_query.assert_not_called()
        manager.db_import.assert_called_once_with(DB, dump, force=True)

    @pytest.mark.parametrize("external", [False, True])
    def test_a_wrong_answer_refuses_on_either_server(self, tmp_path, external):
        orch, manager, dump = self._restorer(tmp_path, external=external, answer="shopdbb")
        with pytest.raises(RestoreNotConfirmed):
            orch._restore_db(SITE, dump, requested=True)
        manager.db_import.assert_not_called()

    def test_the_global_db_prompt_says_whose_database_it_is(self, tmp_path):
        orch, _manager, dump = self._restorer(tmp_path, external=False)
        orch._restore_db(SITE, dump, requested=True)
        assert "fm's own global-db container" in _warnings(orch)

    def test_the_external_prompt_still_says_fm_does_not_own_it(self, tmp_path):
        orch, _manager, dump = self._restorer(tmp_path, external=True)
        orch._restore_db(SITE, dump, requested=True)
        assert "a database fm does not own" in _warnings(orch)

    def test_the_global_db_table_count_is_read_from_the_server(self, tmp_path):
        """The number in the question has to be current, or it is false reassurance."""
        orch, manager, dump = self._restorer(tmp_path, external=False)
        orch._restore_db(SITE, dump, requested=True)
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
        orch._restore_db(SITE, dump)
        manager.db_import.assert_called_once_with(DB, dump, force=True)
        assert "unconfirmed" in _warnings(orch)

    def test_the_migrate_failure_restore_is_the_insurance_path(self, tmp_path):
        """It must NOT be marked requested, or an unattended deploy would lose its
        rollback exactly when it needs it."""
        orch = _rig(tmp_path, switch=PROMISED)
        orch._backup_all = _dumped(orch, SITE)
        dump = orch._backup_all.return_value[SITE]
        orch._migrate.side_effect = _docker_error("patch blew up")
        with pytest.raises(DeployError, match="Migration failed"):
            orch.deploy(NEW_TAG)
        orch._restore_db.assert_called_once_with(SITE, dump)


class TestTheInsuranceCoversEverySite:
    """A failed migrate on a multi-site bench unwinds every schema it dumped, or the bench
    comes back half-migrated with the other half already restored."""

    def _failed_migrate(self, tmp_path, *sites):
        orch = _rig(tmp_path, switch=PROMISED, sites=sites)
        orch._backup_all = _dumped(orch, *sites)
        orch._migrate.side_effect = _docker_error("patch blew up")
        return orch, orch._backup_all.return_value

    def test_every_dump_is_restored_in_reverse_site_order(self, tmp_path):
        """The migrate walks the sites primary-first and stops at the first failure, so the
        unwind runs backwards: the most recently changed schema goes back first."""
        orch, dumps = self._failed_migrate(tmp_path, SITE, OTHER, THIRD)
        with pytest.raises(DeployError, match="Migration failed"):
            orch.deploy(NEW_TAG)
        assert orch._restore_db.call_args_list == [
            call(THIRD, dumps[THIRD]),
            call(OTHER, dumps[OTHER]),
            call(SITE, dumps[SITE]),
        ]

    def test_one_declined_restore_does_not_strand_the_sites_behind_it(self, tmp_path):
        """An external schema whose confirmation is refused is one site's problem. Letting
        it break the loop would leave every site after it on the failed migrate's schema."""
        orch, dumps = self._failed_migrate(tmp_path, SITE, OTHER, THIRD)

        def decline(site, _dump):
            if site == OTHER:
                raise RestoreNotConfirmed(f"{site} was not confirmed")

        orch._restore_db.side_effect = decline
        with pytest.raises(DeployError, match="Migration failed"):
            orch.deploy(NEW_TAG)
        assert [c.args[0] for c in orch._restore_db.call_args_list] == [THIRD, OTHER, SITE]
        assert f"{OTHER} was not confirmed" in _warnings(orch)

    def test_the_declined_site_does_not_swallow_the_migrate_failure(self, tmp_path):
        """The migrate error is the message worth reading; the decline is a warning."""
        orch, _dumps = self._failed_migrate(tmp_path, SITE, OTHER)
        orch._restore_db.side_effect = RestoreNotConfirmed("nobody typed it")
        with pytest.raises(DeployError, match="Migration failed") as exc:
            orch.deploy(NEW_TAG)
        assert "patch blew up" in str(exc.value)


class TestRequestedRestoreThroughDeploy:
    def test_deploy_marks_the_operators_dump_as_requested(self, tmp_path):
        dump = tmp_path / "old.sql"
        orch = _rig(tmp_path, switch=SwitchConfig(migrate=False), backups=MagicMock(return_value={}))
        orch.deploy(NEW_TAG, restore_db_dumps={SITE: dump})
        orch._restore_db.assert_called_once_with(SITE, dump, requested=True, confirmed=False)

    def test_the_yes_bypass_reaches_the_restore(self, tmp_path):
        dump = tmp_path / "old.sql"
        orch = _rig(tmp_path, switch=SwitchConfig(migrate=False), backups=MagicMock(return_value={}))
        orch.deploy(NEW_TAG, restore_db_dumps={SITE: dump}, restore_confirmed=True)
        orch._restore_db.assert_called_once_with(SITE, dump, requested=True, confirmed=True)

    def test_every_recorded_dump_is_restored(self, tmp_path):
        """`fm switch --previous --restore-db` puts the whole bench back, so each site's dump
        goes into that site's schema and each one is confirmed on its own."""
        dumps = {SITE: tmp_path / "shop-old.sql", OTHER: tmp_path / "books-old.sql"}
        orch = _rig(
            tmp_path, switch=SwitchConfig(migrate=False), backups=MagicMock(return_value={}), sites=(SITE, OTHER)
        )
        orch.deploy(NEW_TAG, restore_db_dumps=dumps)
        assert orch._restore_db.call_args_list == [
            call(SITE, dumps[SITE], requested=True, confirmed=False),
            call(OTHER, dumps[OTHER], requested=True, confirmed=False),
        ]

    def test_a_declined_restore_aborts_the_deploy_before_the_swap(self, tmp_path):
        """Unlike the insurance restore, this one is not caught: refusing the import means
        refusing the deploy, and the old stack keeps serving."""
        dump = tmp_path / "old.sql"
        orch = _rig(tmp_path, switch=SwitchConfig(migrate=False), backups=MagicMock(return_value={}))
        orch._restore_db.side_effect = RestoreNotConfirmed("not typed")
        with pytest.raises(RestoreNotConfirmed):
            orch.deploy(NEW_TAG, restore_db_dumps={SITE: dump})
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


def test_a_site_that_could_not_dump_is_absent_not_none(tmp_path):
    """``_backup_site`` deliberately keeps reporting failure as None rather than raising: the
    'auto' probe and the rollback path need different verdicts from the same function, so the
    promise-keeping decision belongs to ``deploy()``. ``_backup_all`` then LEAVES that site out
    instead of mapping it to None, so `if db_dumps` and the missing-site list mean what they
    look like and no caller has to filter Nones back out."""
    orch, _managers = _real_backup_rig(tmp_path, sites=(SITE, OTHER), dumps=(OTHER,))
    dumps = orch._backup_all(tmp_path / "out")
    assert set(dumps) == {OTHER}
    assert inspect.signature(orch._backup_site).return_annotation in (Path | None, "Path | None")


def test_a_stopped_frappe_dumps_nothing_at_all(tmp_path):
    """The container-wide give-up is empty, not one entry per site: there is no client to
    export through, so every site is missing and the guard names all of them."""
    orch, _managers = _real_backup_rig(tmp_path, sites=(SITE, OTHER), running=False)
    assert orch._backup_all(tmp_path / "out") == {}
