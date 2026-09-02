"""Characterization tests for the migration contract and for the v0.20.0 migration.

``MigrationBase`` is the lifecycle every migration inherits: which steps run in which
order during ``up``, which benches a run refuses to touch and why, what is recorded
when a bench blows up mid-migration, and how ``down`` unwinds a run. Those decisions
are the contract; a migration author only fills in ``migrate_bench`` /
``migrate_services``. ``MigrationV0200`` is the newest migration and the concrete
example of that contract: what it rewrites on disk, and what it deliberately leaves
alone.

These tests pin TODAY's behaviour so both modules can be refactored safely. Nothing
here touches Docker, the network, a real bench or a real ``~/frappe``: every
collaborator that would is mocked at its boundary and every path lives under
``tmp_path``. Where the pinned behaviour looks wrong it is still pinned as-is, with a
comment saying so.
"""

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tomlkit
from ruamel.yaml import YAML

from frappe_manager import GLOBAL_DB_IMAGE
from frappe_manager.migration_manager.backup_manager import BackupManager
from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.migration_constants import DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS
from frappe_manager.migration_manager.migration_exceptions import MigrationExceptionInBench
from frappe_manager.migration_manager.migrations.migrate_0_20_0 import ADMINER_VOLUMES, MigrationV0200
from frappe_manager.migration_manager.version import Version
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo

BASE = "frappe_manager.migration_manager.migration_base"
V0200 = "frappe_manager.migration_manager.migrations.migrate_0_20_0"


# --------------------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------------------


class _FakeBench:
    """Stands in for MigrationBench: only name/path/docker/compose/site_names are used here."""

    def __init__(self, name, path, output=None, sites=None):
        self.name = name
        self.path = Path(path)
        self.output = output
        self.docker = MagicMock()
        self.compose_file_manager = MagicMock()
        # The real property reads `[sites]` out of bench_config.toml and falls back to the bench's
        # own name when there is no table, which is the pre-decoupling shape every migration up to
        # 0.20.0 was written against. Defaulting to that fallback keeps these tests describing a
        # pre-decoupling bench unless one explicitly asks for several sites.
        self.site_names = list(sites) if sites else [name]


def _executor(
    *,
    target_benches=None,
    exclude=(),
    rerun=False,
    migrate_benches=None,
    infra=False,
    skip_backup=False,
    skip_backup_for=(),
):
    """A migration executor stub with every flag the base class reads set explicitly."""
    executor = MagicMock()
    executor.fm_infrastructure_needs_migration = infra
    executor.target_benches = target_benches
    executor.exclude_benches = list(exclude)
    executor.rerun = rerun
    executor.migrate_benches = {} if migrate_benches is None else migrate_benches
    executor.skip_backup = skip_backup
    executor.skip_backup_for = list(skip_backup_for)
    return executor


class _LifecycleMigration(MigrationBase):
    """Records the ``up``/``down`` step order instead of doing any real work."""

    version = Version("0.9.0")

    def __init__(self, output_handler, calls):
        super().__init__(output_handler=output_handler)
        self.calls = calls

    def init(self):
        self.calls.append("init")
        self.backup_manager = MagicMock(backups=[])
        self.benches_manager = MagicMock()
        self.services_manager = MagicMock()

    def services_basic_backup(self):
        self.calls.append("services_basic_backup")

    def migrate_services(self):
        self.calls.append("migrate_services")

    def undo_services_migrate(self):
        self.calls.append("undo_services_migrate")

    def migrate_benches(self):
        self.calls.append("migrate_benches")

    def undo_bench_migrate(self, bench):
        self.calls.append(("undo_bench_migrate", bench.name))


class _BenchLoopMigration(MigrationBase):
    """Keeps the real ``migrate_benches`` loop; records/fails the per-bench hooks."""

    version = Version("0.9.0")

    def __init__(self, output_handler, calls, fail_for=()):
        super().__init__(output_handler=output_handler)
        self.calls = calls
        self.fail_for = set(fail_for)

    def bench_basic_backup(self, bench):
        self.calls.append(("bench_basic_backup", bench.name))

    def migrate_bench(self, bench):
        self.calls.append(("migrate_bench", bench.name))
        if bench.name in self.fail_for:
            raise RuntimeError(f"boom in {bench.name}")

    def undo_bench_migrate(self, bench):
        self.calls.append(("undo_bench_migrate", bench.name))


class _PlainMigration(MigrationBase):
    """No hooks overridden: exercises the base implementations themselves."""

    version = Version("0.9.0")


@pytest.fixture
def output():
    return MagicMock()


@pytest.fixture
def calls():
    return []


def _write_bench(benches_dir: Path, name: str, migrated_to: str | None = None) -> Path:
    """A bench dir shaped the way ``MigrationBenches.get_all_benches`` expects."""
    bench_path = benches_dir / name
    bench_path.mkdir(parents=True, exist_ok=True)
    (bench_path / "docker-compose.yml").write_text("services: {}\n")
    if migrated_to:
        (bench_path / "bench_config.toml").write_text(f'[migration_state]\nmigrated_to = "{migrated_to}"\n')
    return bench_path


def _backup_manager(tmp_path: Path) -> BackupManager:
    """A real BackupManager confined to tmp_path (its default is ~/frappe/backups)."""
    return BackupManager(
        name="0.9.0",
        benches_dir=tmp_path / "sites",
        backup_dir=tmp_path / "backups",
    )


# --------------------------------------------------------------------------------------
# image tag / dev detection / executor wiring
# --------------------------------------------------------------------------------------


def test_dev_environment_migrates_with_the_running_image_tag(output):
    # A dev checkout must not pull the released tag for the version it is migrating to:
    # that image does not exist yet.
    migration = _PlainMigration(output_handler=output)
    migration.is_dev_environment = True
    migration.effective_image_tag = "v0.9.0.dev3"

    assert migration._get_image_tag_for_migration() == "v0.9.0.dev3"


def test_stable_environment_migrates_with_the_migration_version_tag(output):
    migration = _PlainMigration(output_handler=output)
    migration.is_dev_environment = False
    migration.effective_image_tag = "v0.9.0.dev3"

    assert migration._get_image_tag_for_migration() == "v0.9.0"


@pytest.mark.parametrize(
    ("fm_version", "is_dev"),
    [
        ("0.20.0.dev0", True),
        ("0.20.0rc1", True),
        ("0.20.0a1", True),
        ("0.20.0", False),
        ("0.20.0.post1", False),
    ],
)
def test_dev_environment_is_decided_from_the_running_fm_version(output, fm_version, is_dev):
    migration = _PlainMigration(output_handler=output)
    migration._current_fm_version = fm_version

    assert migration._detect_dev_environment() is is_dev


def test_rollback_version_is_the_migrations_own_version(output):
    # The executor uses this to decide how far back to rewind.
    assert _PlainMigration(output_handler=output).get_rollback_version() == Version("0.9.0")


def test_a_migration_that_overrides_nothing_is_inert(output, tmp_path):
    # The four hooks a migration fills in are no-ops on the base class, so a migration
    # that only needs one of them cannot accidentally act on the others.
    migration = _PlainMigration(output_handler=output)
    bench = _FakeBench("alpha", tmp_path)

    assert migration.migrate_services() is None
    assert migration.undo_services_migrate() is None
    assert migration.migrate_bench(bench) is None
    assert migration.undo_bench_migrate(bench) is None
    output.print.assert_not_called()


def test_setting_the_executor_adopts_its_output_handler(output):
    migration = _PlainMigration(output_handler=output)
    executor = _executor()

    migration.set_migration_executor(executor)

    assert migration.migration_executor is executor
    assert migration.output is executor.output


def test_an_executor_without_an_output_handler_leaves_ours_in_place(output):
    class _NoOutput:
        pass

    migration = _PlainMigration(output_handler=output)
    executor = _NoOutput()

    migration.set_migration_executor(executor)

    assert migration.migration_executor is executor
    assert migration.output is output


def test_init_builds_the_collaborators_from_the_migration_version_and_benches_dir(output, tmp_path):
    migration = _PlainMigration(output_handler=output)
    migration.benches_dir = tmp_path / "sites"

    with (
        patch(f"{BASE}.BackupManager") as backup_manager,
        patch(f"{BASE}.MigrationBenches") as benches,
        patch(f"{BASE}.MigrationServicesManager") as services,
    ):
        migration.init()

    backup_manager.assert_called_once_with(name="0.9.0", benches_dir=tmp_path / "sites")
    benches.assert_called_once_with(tmp_path / "sites")
    assert services.call_count == 1
    assert migration.backup_manager is backup_manager.return_value
    assert migration.benches_manager is benches.return_value


# --------------------------------------------------------------------------------------
# up(): which steps run, and in which order
# --------------------------------------------------------------------------------------


def test_a_skipped_migration_short_circuits_before_doing_anything(output, calls):
    migration = _LifecycleMigration(output_handler=output, calls=calls)
    migration.skip = True
    migration.migration_executor = _executor(infra=True)

    assert migration.up() is True
    assert calls == []
    output.print.assert_not_called()


def test_up_backs_up_services_before_migrating_them_and_benches_come_last(output, calls):
    migration = _LifecycleMigration(output_handler=output, calls=calls)
    migration.migration_executor = _executor(infra=True)

    assert migration.up() is None
    assert calls == ["init", "services_basic_backup", "migrate_services", "migrate_benches"]


def test_up_skips_the_service_steps_when_the_infrastructure_needs_no_migration(output, calls):
    migration = _LifecycleMigration(output_handler=output, calls=calls)
    migration.migration_executor = _executor(infra=False)

    migration.up()

    assert calls == ["init", "migrate_benches"]


def test_up_without_an_executor_still_migrates_benches_but_not_services(output, calls):
    migration = _LifecycleMigration(output_handler=output, calls=calls)
    migration.migration_executor = None

    migration.up()

    assert calls == ["init", "migrate_benches"]


# --------------------------------------------------------------------------------------
# down(): how a run is unwound
# --------------------------------------------------------------------------------------


def test_down_undoes_only_the_benches_that_did_not_fail(output, calls):
    # A bench whose migration raised was already restored inline by migrate_benches;
    # undoing it twice is what this guard prevents.
    migration = _LifecycleMigration(output_handler=output, calls=calls)
    ok, broken = _FakeBench("ok", Path("/ok")), _FakeBench("broken", Path("/broken"))
    migration.migration_executor = _executor(
        migrate_benches={
            "ok": {"object": ok, "exception": None},
            "broken": {"object": broken, "exception": RuntimeError("boom")},
        },
    )
    migration.backup_manager = MagicMock(backups=[])

    migration.down()

    assert ("undo_bench_migrate", "ok") in calls
    assert ("undo_bench_migrate", "broken") not in calls


def test_down_restores_every_backup_forcefully_then_cleans_up_and_undoes_services(output, calls):
    migration = _LifecycleMigration(output_handler=output, calls=calls)
    migration.migration_executor = _executor(migrate_benches={})

    order = []
    first, second = MagicMock(name="first"), MagicMock(name="second")
    backup_manager = MagicMock(backups=[first, second])
    backup_manager.restore.side_effect = lambda backup, force: order.append(("restore", backup, force))
    backup_manager.cleanup_new_files.side_effect = lambda: order.append(("cleanup",))
    migration.backup_manager = backup_manager

    migration.down()

    assert order == [("restore", first, True), ("restore", second, True), ("cleanup",)]
    # Services are unwound only after the files are back on disk.
    assert calls == ["undo_services_migrate"]


# --------------------------------------------------------------------------------------
# services_basic_backup(): the guard that refuses to migrate services blind
# --------------------------------------------------------------------------------------


def test_missing_services_compose_refuses_the_service_migration(output, tmp_path):
    migration = _PlainMigration(output_handler=output)
    migration.backup_manager = _backup_manager(tmp_path)
    migration.services_manager = MagicMock()
    migration.services_manager.compose_file_manager.exists.return_value = False

    with pytest.raises(MigrationExceptionInBench, match="not found"):
        migration.services_basic_backup()

    assert migration.backup_manager.backups == []


def test_the_services_compose_is_backed_up_before_it_is_touched(output, tmp_path):
    compose_path = tmp_path / "services" / "docker-compose.yml"
    compose_path.parent.mkdir(parents=True)
    compose_path.write_text("services: {}\n")

    migration = _PlainMigration(output_handler=output)
    migration.backup_manager = _backup_manager(tmp_path)
    migration.services_manager = MagicMock()
    migration.services_manager.compose_file_manager.exists.return_value = True
    migration.services_manager.compose_file_manager.compose_path = compose_path

    migration.services_basic_backup()

    assert [b.src for b in migration.backup_manager.backups] == [compose_path]
    assert migration.backup_manager.backups[0].real_dest.read_text() == "services: {}\n"


# --------------------------------------------------------------------------------------
# migrate_benches(): every guard that decides a bench is not migrated
# --------------------------------------------------------------------------------------


@pytest.fixture
def bench_loop(output, calls, tmp_path):
    """A migration whose bench loop runs for real over benches on disk."""

    def _build(bench_names, *, fail_for=(), migrated_to=None, **executor_kwargs):
        migration = _BenchLoopMigration(output_handler=output, calls=calls, fail_for=fail_for)
        migration.benches_dir = tmp_path / "sites"
        migration.benches_dir.mkdir(parents=True, exist_ok=True)
        for name in bench_names:
            _write_bench(migration.benches_dir, name, migrated_to=migrated_to)
        migration.init = lambda: None  # type: ignore[method-assign]
        from frappe_manager.migration_manager.migration_helpers import MigrationBenches

        migration.benches_manager = MigrationBenches(migration.benches_dir)
        migration.backup_manager = _backup_manager(tmp_path)
        migration.migration_executor = _executor(**executor_kwargs)
        return migration

    return _build


def test_an_infrastructure_only_migration_touches_no_bench(bench_loop, calls):
    # target_benches None is how the executor says "services only".
    migration = bench_loop(["alpha", "beta"], target_benches=None)

    migration.migrate_benches()

    assert calls == []


def test_a_bench_outside_the_target_list_is_not_migrated(bench_loop, calls):
    migration = bench_loop(["alpha", "beta"], target_benches=["alpha"])

    migration.migrate_benches()

    assert calls == [("bench_basic_backup", "alpha"), ("migrate_bench", "alpha")]


def test_an_excluded_bench_is_skipped_and_told_so(bench_loop, calls, output):
    migration = bench_loop(["alpha", "beta"], target_benches=["alpha", "beta"], exclude=["beta"])

    migration.migrate_benches()

    assert [c for c in calls if c[1] == "beta"] == []
    assert any("beta" in str(c.args) and "--exclude-bench" in str(c.args) for c in output.print.call_args_list)


def test_a_bench_already_at_the_migration_version_is_skipped(bench_loop, calls, output):
    migration = bench_loop(["alpha"], target_benches=["alpha"], migrated_to="0.9.0")

    migration.migrate_benches()

    assert calls == []
    assert any("already at v0.9.0" in str(c.args) for c in output.print.call_args_list)


def test_a_bench_beyond_the_migration_version_is_skipped(bench_loop, calls):
    migration = bench_loop(["alpha"], target_benches=["alpha"], migrated_to="1.5.0")

    migration.migrate_benches()

    assert calls == []


def test_rerun_forces_a_bench_that_is_already_at_the_version(bench_loop, calls):
    migration = bench_loop(["alpha"], target_benches=["alpha"], migrated_to="0.9.0", rerun=True)

    migration.migrate_benches()

    assert calls == [("bench_basic_backup", "alpha"), ("migrate_bench", "alpha")]


def test_a_bench_below_the_migration_version_is_migrated(bench_loop, calls):
    migration = bench_loop(["alpha"], target_benches=["alpha"], migrated_to="0.8.0")

    migration.migrate_benches()

    assert calls == [("bench_basic_backup", "alpha"), ("migrate_bench", "alpha")]


def test_a_bench_that_failed_an_earlier_migration_is_skipped_and_fails_the_run(bench_loop, calls, output):
    migration = bench_loop(
        ["alpha"],
        target_benches=["alpha"],
        migrate_benches={"alpha": {"object": None, "exception": RuntimeError("earlier")}},
    )

    with pytest.raises(MigrationExceptionInBench):
        migration.migrate_benches()

    assert calls == []
    assert any("failed bench" in str(c.args) for c in output.print.call_args_list)


def test_a_bench_recorded_without_an_exception_is_migrated_normally(bench_loop, calls):
    migration = bench_loop(
        ["alpha"],
        target_benches=["alpha"],
        migrate_benches={"alpha": {"object": None, "exception": None}},
    )

    migration.migrate_benches()

    assert calls == [("bench_basic_backup", "alpha"), ("migrate_bench", "alpha")]


def test_bench_state_is_recorded_before_the_bench_is_touched(bench_loop, calls):
    migration = bench_loop(["alpha"], target_benches=["alpha"])

    migration.migrate_benches()

    bench, kwargs = (
        migration.migration_executor.set_bench_data.call_args.args,
        migration.migration_executor.set_bench_data.call_args.kwargs,
    )
    assert bench[0].name == "alpha"
    assert kwargs == {"migration_version": Version("0.9.0")}
    assert calls == [("bench_basic_backup", "alpha"), ("migrate_bench", "alpha")]


def test_the_bench_object_carries_the_migrations_output_handler(bench_loop, output, tmp_path):
    migration = bench_loop(["alpha"], target_benches=["alpha"])

    with patch(f"{BASE}.MigrationBench", side_effect=_FakeBench) as bench_cls:
        migration.migrate_benches()

    bench_cls.assert_called_once_with(name="alpha", path=tmp_path / "sites" / "alpha", output=output)


# --------------------------------------------------------------------------------------
# migrate_benches(): what happens when a bench blows up
# --------------------------------------------------------------------------------------


def test_a_failing_bench_is_recorded_undone_and_fails_the_whole_run(bench_loop, calls):
    migration = bench_loop(["alpha"], target_benches=["alpha"], fail_for=["alpha"])

    with pytest.raises(MigrationExceptionInBench):
        migration.migrate_benches()

    assert calls[-1] == ("undo_bench_migrate", "alpha")
    # Second call records the exception object together with the version.
    failure = migration.migration_executor.set_bench_data.call_args_list[-1]
    assert isinstance(failure.args[1], RuntimeError)
    assert failure.args[2] == Version("0.9.0")


def test_a_failing_bench_only_restores_its_own_backups(bench_loop, tmp_path, output, calls):
    migration = bench_loop(["alpha"], target_benches=["alpha"], fail_for=["alpha"])
    restored = []
    mine = MagicMock(bench="alpha")
    someone_elses = MagicMock(bench="beta")
    migration.backup_manager = MagicMock(backups=[mine, someone_elses])
    migration.backup_manager.restore.side_effect = lambda backup, force: restored.append((backup, force))

    with pytest.raises(MigrationExceptionInBench):
        migration.migrate_benches()

    assert restored == [(mine, True)]


def test_a_failing_bench_is_brought_down_with_orphans_removed_and_volumes_kept(bench_loop):
    migration = bench_loop(["alpha"], target_benches=["alpha"], fail_for=["alpha"])
    built = []

    def _bench(name, path, output=None):
        built.append(_FakeBench(name, path, output))
        return built[-1]

    with patch(f"{BASE}.MigrationBench", side_effect=_bench), pytest.raises(MigrationExceptionInBench):
        migration.migrate_benches()

    built[0].docker.compose.down.assert_called_once_with(
        remove_orphans=True,
        volumes=False,
        timeout=DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS,
        stream=True,
    )


def test_a_docker_failure_while_cleaning_up_does_not_mask_the_migration_error(bench_loop):
    migration = bench_loop(["alpha"], target_benches=["alpha"], fail_for=["alpha"])

    def _bench(name, path, output=None):
        bench = _FakeBench(name, path, output)
        bench.docker.compose.down.side_effect = RuntimeError("docker is down")
        return bench

    with patch(f"{BASE}.MigrationBench", side_effect=_bench), pytest.raises(MigrationExceptionInBench):
        migration.migrate_benches()


def test_one_failing_bench_does_not_stop_the_others(bench_loop, calls):
    migration = bench_loop(["alpha", "beta"], target_benches=["alpha", "beta"], fail_for=["alpha"])

    with pytest.raises(MigrationExceptionInBench):
        migration.migrate_benches()

    assert ("migrate_bench", "beta") in calls


# --------------------------------------------------------------------------------------
# bench_basic_backup(): which files are saved, and the skips
# --------------------------------------------------------------------------------------


@pytest.fixture
def backed_up_bench(tmp_path):
    """A bench with every file bench_basic_backup knows how to save."""
    bench_path = tmp_path / "sites" / "alpha"
    sites = bench_path / "workspace" / "frappe-bench" / "sites"
    (sites / "alpha").mkdir(parents=True)
    (bench_path / "bench_config.toml").write_text('db_name = "alpha_db"\n')
    (bench_path / "docker-compose.yml").write_text("services: {}\n")
    (sites / "common_site_config.json").write_text(json.dumps({"db_host": "global-db"}))
    (sites / "alpha" / "site_config.json").write_text(
        json.dumps({"db_name": "alpha_db", "db_password": "secret", "db_host": "global-db"}),
    )
    return _FakeBench("alpha", bench_path)


@pytest.fixture
def backup_migration(output, tmp_path):
    migration = _PlainMigration(output_handler=output)
    migration.benches_dir = tmp_path / "sites"
    migration.backup_manager = _backup_manager(tmp_path)
    migration.migration_executor = _executor()
    return migration


def test_skip_all_backup_saves_nothing_for_the_bench(backup_migration, backed_up_bench, output):
    backup_migration.migration_executor.skip_backup = True

    backup_migration.bench_basic_backup(backed_up_bench)

    assert backup_migration.backup_manager.backups == []
    output.warning.assert_called_once()


def test_a_per_bench_backup_skip_saves_nothing_for_that_bench(backup_migration, backed_up_bench):
    backup_migration.migration_executor.skip_backup_for = ["alpha"]

    backup_migration.bench_basic_backup(backed_up_bench)

    assert backup_migration.backup_manager.backups == []


def test_backup_covers_the_bench_config_compose_and_both_site_configs(backup_migration, backed_up_bench):
    with patch.object(backup_migration, "bench_db_backup") as db_backup:
        backup_migration.bench_basic_backup(backed_up_bench)

    assert [b.src.name for b in backup_migration.backup_manager.backups] == [
        "bench_config.toml",
        "docker-compose.yml",
        "common_site_config.json",
        "site_config.json",
    ]
    assert db_backup.call_count == 1


def test_a_bench_without_a_bench_config_backs_up_the_rest(backup_migration, backed_up_bench):
    (backed_up_bench.path / "bench_config.toml").unlink()

    with patch.object(backup_migration, "bench_db_backup"):
        backup_migration.bench_basic_backup(backed_up_bench)

    assert [b.src.name for b in backup_migration.backup_manager.backups] == [
        "docker-compose.yml",
        "common_site_config.json",
        "site_config.json",
    ]


def test_the_db_backup_gets_the_benchs_own_docker_and_compose_file(backup_migration, backed_up_bench):
    with patch.object(backup_migration, "bench_db_backup") as db_backup:
        backup_migration.bench_basic_backup(backed_up_bench)

    kwargs = db_backup.call_args.kwargs
    assert kwargs["bench"] is backed_up_bench
    assert kwargs["bench_docker"] is backed_up_bench.docker
    assert kwargs["bench_compose_file"] is backed_up_bench.compose_file_manager
    assert kwargs["backup_manager"] is backup_migration.backup_manager
    # db_info is read off disk, not out of the bench object.
    assert kwargs["db_info"].name == "alpha_db"


# --------------------------------------------------------------------------------------
# every recorded site, not just the one named after the bench
# --------------------------------------------------------------------------------------


@pytest.fixture
def two_site_bench(tmp_path):
    """A DECOUPLED bench: named `shop`, serving `shop.localhost` and `b.example.com`.

    Neither site is called `shop`, which is what broke the old code: it read
    `sites/shop/site_config.json`, found nothing, and `DatabaseServerServiceInfo` raised a
    ValidationError with no `name` or `user` to build from, so the migration aborted before backing
    up anything at all.
    """
    bench_path = tmp_path / "sites" / "shop"
    sites = bench_path / "workspace" / "frappe-bench" / "sites"
    for site, schema in (("shop.localhost", "shop_db"), ("b.example.com", "b_db")):
        (sites / site).mkdir(parents=True)
        (sites / site / "site_config.json").write_text(
            json.dumps({"db_name": schema, "db_password": "secret", "db_host": "global-db"}),
        )
    (bench_path / "bench_config.toml").write_text(
        'name = "shop"\n[sites."shop.localhost"]\n[sites."b.example.com"]\n'
    )
    (bench_path / "docker-compose.yml").write_text("services: {}\n")
    (sites / "common_site_config.json").write_text(json.dumps({"db_host": "global-db"}))
    return _FakeBench("shop", bench_path, sites=["shop.localhost", "b.example.com"])


def test_every_recorded_site_gets_its_config_and_its_database_backed_up(backup_migration, two_site_bench):
    with patch.object(backup_migration, "bench_db_backup") as db_backup:
        backup_migration.bench_basic_backup(two_site_bench)

    assert [b.src.name for b in backup_migration.backup_manager.backups] == [
        "bench_config.toml",
        "docker-compose.yml",
        "common_site_config.json",
        "site_config.json",
        "site_config.json",
    ]
    assert [c.kwargs["site"] for c in db_backup.call_args_list] == ["shop.localhost", "b.example.com"]


def test_each_site_is_dumped_from_its_own_schema(backup_migration, two_site_bench):
    """The whole point of backing up per site: one dump per schema. Reading the endpoint off the
    bench name gave both sites the first site's credentials, or nothing at all."""
    with patch.object(backup_migration, "bench_db_backup") as db_backup:
        backup_migration.bench_basic_backup(two_site_bench)

    assert [c.kwargs["db_info"].name for c in db_backup.call_args_list] == ["shop_db", "b_db"]


def test_a_recorded_site_with_no_config_on_disk_is_reported_and_skipped(backup_migration, two_site_bench, output):
    """A recorded site whose directory never got made must not abort the whole migration: the other
    sites still need their backups, and the operator needs to know which one was missed."""
    (two_site_bench.path / "workspace" / "frappe-bench" / "sites" / "b.example.com" / "site_config.json").unlink()

    with patch.object(backup_migration, "bench_db_backup") as db_backup:
        backup_migration.bench_basic_backup(two_site_bench)

    assert [c.kwargs["site"] for c in db_backup.call_args_list] == ["shop.localhost"]
    warned = " ".join(str(c) for c in output.warning.call_args_list)
    assert "b.example.com" in warned


def test_a_pre_decoupling_bench_still_backs_up_exactly_one_site(backup_migration, backed_up_bench):
    """`alpha` has no `[sites]` table, so the fallback names the bench's own site. Every migration
    up to 0.20.0 runs on benches of this shape and must behave exactly as it did."""
    with patch.object(backup_migration, "bench_db_backup") as db_backup:
        backup_migration.bench_basic_backup(backed_up_bench)

    assert [c.kwargs["site"] for c in db_backup.call_args_list] == ["alpha"]

# --------------------------------------------------------------------------------------
# _resolve_database_name / _resolve_mysql_home
# --------------------------------------------------------------------------------------


def _db_info(name=None):
    return DatabaseServerServiceInfo(host="global-db", user="u", port=3306, password="p", name=name)


def test_the_site_config_database_name_wins_over_bench_config(backup_migration, backed_up_bench):
    (backed_up_bench.path / "bench_config.toml").write_text('db_name = "from_toml"\n')

    assert backup_migration._resolve_database_name(backed_up_bench, _db_info("from_site_config")) == "from_site_config"


def test_the_database_name_falls_back_to_bench_config(backup_migration, backed_up_bench):
    assert backup_migration._resolve_database_name(backed_up_bench, _db_info(None)) == "alpha_db"


def test_no_database_name_anywhere_resolves_to_none(backup_migration, backed_up_bench):
    (backed_up_bench.path / "bench_config.toml").write_text("name = 'alpha'\n")

    assert backup_migration._resolve_database_name(backed_up_bench, _db_info(None)) is None


def test_an_unreadable_bench_config_warns_and_resolves_no_database_name(backup_migration, backed_up_bench, output):
    (backed_up_bench.path / "bench_config.toml").write_text("this is not = = toml\n")

    assert backup_migration._resolve_database_name(backed_up_bench, _db_info(None)) is None
    assert "Failed to read db_name" in output.warning.call_args.args[0]


def test_a_bench_without_a_config_has_no_mysql_home(backup_migration, backed_up_bench):
    (backed_up_bench.path / "bench_config.toml").unlink()

    assert backup_migration._resolve_mysql_home(backed_up_bench) is None


def test_a_bench_missing_from_the_database_table_has_no_mysql_home(backup_migration, backed_up_bench):
    # No [database] table at all, and a table for a different bench: both mean
    # "this bench uses global-db", which needs no TLS client config.
    assert backup_migration._resolve_mysql_home(backed_up_bench) is None

    (backed_up_bench.path / "bench_config.toml").write_text('[database]\n[database.other]\nhost = "db.example"\n')
    assert backup_migration._resolve_mysql_home(backed_up_bench) is None


def test_an_external_database_bench_gets_its_tls_dir_as_mysql_home(backup_migration, backed_up_bench):
    (backed_up_bench.path / "bench_config.toml").write_text('[database]\n[database.alpha]\nhost = "db.example"\n')

    assert backup_migration._resolve_mysql_home(backed_up_bench) == "/workspace/frappe-bench/config/tls/alpha"


def test_an_unparsable_config_warns_and_yields_no_mysql_home(backup_migration, backed_up_bench, output):
    (backed_up_bench.path / "bench_config.toml").write_text("[database\n")

    assert backup_migration._resolve_mysql_home(backed_up_bench) is None
    assert "Failed to read database table" in output.warning.call_args.args[0]


# --------------------------------------------------------------------------------------
# bench_db_backup(): the dump, and the prompt when there is nothing to dump
# --------------------------------------------------------------------------------------


def test_an_unknown_database_name_asks_before_skipping_the_dump(backup_migration, backed_up_bench, output):
    (backed_up_bench.path / "bench_config.toml").unlink()
    output.prompt_ask.return_value = "yes"

    with patch(f"{BASE}.MariaDBManager") as manager:
        backup_migration.bench_db_backup(
            bench=backed_up_bench,
            db_info=_db_info(None),
            bench_docker=backed_up_bench.docker,
            bench_compose_file=backed_up_bench.compose_file_manager,
            backup_manager=backup_migration.backup_manager,
        )

    manager.assert_not_called()
    assert output.prompt_ask.call_args.kwargs["choices"] == ["yes", "no"]
    assert "--skip-all-backup" in output.prompt_ask.call_args.kwargs["required_flag"]


def test_declining_the_skip_aborts_the_migration_for_that_bench(backup_migration, backed_up_bench, output):
    (backed_up_bench.path / "bench_config.toml").unlink()
    output.prompt_ask.return_value = "no"

    with pytest.raises(MigrationExceptionInBench, match="Migration aborted for alpha"):
        backup_migration.bench_db_backup(
            bench=backed_up_bench,
            db_info=_db_info(None),
            bench_docker=backed_up_bench.docker,
            bench_compose_file=backed_up_bench.compose_file_manager,
            backup_manager=backup_migration.backup_manager,
        )

    output.display_error.assert_called_once()


def test_the_dump_is_handed_over_through_logs_and_kept_gzipped(backup_migration, backed_up_bench, tmp_path):
    """The transit directory is `frappe-bench/logs`, and that is not cosmetic.

    It used to be `workspace/.cache`, which the host only sees when the WHOLE workspace is
    bind-mounted, i.e. mount runtime. An image bench mounts only its data paths, so the dump was
    written into the container's own filesystem and the migration aborted claiming the database
    export had failed. `logs` is the one writable directory both runtimes mount.
    """
    cache_dir = backed_up_bench.path / "workspace" / "frappe-bench" / "logs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    backup_manager = backup_migration.backup_manager
    # The gz lands in the per-migration bench backup dir, which earlier backup steps
    # in bench_basic_backup are what actually create.
    gz_dir = backed_up_bench.path / backup_manager.bench_backup_dir / "0.9.0"
    gz_dir.mkdir(parents=True)

    def _export(db_name, container_path):
        # The container path is the same file the host sees through the workspace mount.
        (cache_dir / Path(container_path).name).write_text(f"dump of {db_name}")

    with patch(f"{BASE}.MariaDBManager") as manager_cls:
        manager_cls.return_value.db_export.side_effect = _export
        backup_migration.bench_db_backup(
            bench=backed_up_bench,
            db_info=_db_info("alpha_db"),
            bench_docker=backed_up_bench.docker,
            bench_compose_file=backed_up_bench.compose_file_manager,
            backup_manager=backup_manager,
        )

    db_name, container_path = manager_cls.return_value.db_export.call_args.args
    assert db_name == "alpha_db"
    assert container_path.parent == Path("/workspace/frappe-bench/logs")

    gzipped = list(gz_dir.glob("db-alpha-*.sql.gz"))
    assert len(gzipped) == 1
    with gzip.open(gzipped[0], "rb") as f:
        assert f.read() == b"dump of alpha_db"
    # The plaintext dump does not survive the backup.
    assert list(cache_dir.iterdir()) == []


def test_the_dump_client_runs_in_the_frappe_service_with_the_benchs_tls_home(backup_migration, backed_up_bench):
    (backed_up_bench.path / "bench_config.toml").write_text('[database]\n[database.alpha]\nhost = "db.example"\n')
    cache_dir = backed_up_bench.path / "workspace" / "frappe-bench" / "logs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (backed_up_bench.path / backup_migration.backup_manager.bench_backup_dir / "0.9.0").mkdir(parents=True)

    db_info = _db_info("alpha_db")
    with patch(f"{BASE}.MariaDBManager") as manager_cls:
        manager_cls.return_value.db_export.side_effect = lambda name, path: (cache_dir / Path(path).name).write_text(
            "x"
        )
        backup_migration.bench_db_backup(
            bench=backed_up_bench,
            db_info=db_info,
            bench_docker=backed_up_bench.docker,
            bench_compose_file=backed_up_bench.compose_file_manager,
            backup_manager=backup_migration.backup_manager,
        )

    args, kwargs = manager_cls.call_args
    assert args[0] is db_info
    assert kwargs == {"run_on_compose_service": "frappe", "mysql_home": "/workspace/frappe-bench/config/tls/alpha"}


def test_a_missing_migration_backup_dir_makes_the_db_backup_fail(backup_migration, backed_up_bench):
    # SUSPICION, pinned not fixed: bench_db_backup never creates the directory it
    # gzips into, so it only works because bench_basic_backup's file backups made it
    # first. Called on a bench with none of those files, it raises FileNotFoundError
    # rather than reporting a backup problem.
    cache_dir = backed_up_bench.path / "workspace" / "frappe-bench" / "logs"
    cache_dir.mkdir(parents=True, exist_ok=True)

    with patch(f"{BASE}.MariaDBManager") as manager_cls:
        manager_cls.return_value.db_export.side_effect = lambda name, path: (cache_dir / Path(path).name).write_text(
            "x"
        )
        with pytest.raises(FileNotFoundError):
            backup_migration.bench_db_backup(
                bench=backed_up_bench,
                db_info=_db_info("alpha_db"),
                bench_docker=backed_up_bench.docker,
                bench_compose_file=backed_up_bench.compose_file_manager,
                backup_manager=backup_migration.backup_manager,
            )


def test_a_dump_that_never_reaches_the_host_is_reported_as_a_mount_problem(backup_migration, backed_up_bench):
    """The image-runtime failure, named for what it is.

    An export that "succeeds" while the file never appears on the host means the container is not
    sharing the transit directory. Before the guard, the next line gzipped a path that did not
    exist and the operator got a FileNotFoundError naming neither the bench nor the cause; the
    migration reported a DB export failure and sent them looking at the database.
    """
    (backed_up_bench.path / "workspace" / "frappe-bench" / "logs").mkdir(parents=True, exist_ok=True)
    (backed_up_bench.path / backup_migration.backup_manager.bench_backup_dir / "0.9.0").mkdir(parents=True)

    with patch(f"{BASE}.MariaDBManager") as manager_cls:
        # Exports without error, writes nothing the host can see: exactly an image bench.
        manager_cls.return_value.db_export.side_effect = lambda name, path: None
        with pytest.raises(MigrationExceptionInBench, match="did not reach the host"):
            backup_migration.bench_db_backup(
                bench=backed_up_bench,
                db_info=_db_info("alpha_db"),
                bench_docker=backed_up_bench.docker,
                bench_compose_file=backed_up_bench.compose_file_manager,
                backup_manager=backup_migration.backup_manager,
            )


def test_the_transit_path_is_one_both_runtimes_mount(backup_migration, backed_up_bench):
    # Pins the coupling: the migration's transit directory must be a path image runtime actually
    # binds. If `data_binds` ever stops mounting logs, this fails here rather than at 3am.
    from frappe_manager.site_manager.modules.compose_shape import container_transit_path, data_binds

    container_path, _ = container_transit_path("db-x.sql")
    mounted = {b.container for b in data_binds(["alpha"])}
    assert str(container_path.parent) in mounted


# ======================================================================================
# MigrationV0200
# ======================================================================================

_ADMIN_TOOLS_COMPOSE = """\
x-version: '0.19.0'
services:
  adminer:
    image: adminer:4
    environment:
      ADMINER_DEFAULT_SERVER: global-db
    volumes:
      - ./old:/old
    restart: always
"""


@pytest.fixture
def no_frontend_subnet(tmp_path):
    """Neither the services compose nor a running network yields a subnet, so the
    real-ip step is a no-op: keeps the admin-tools tests to one subject."""
    empty = tmp_path / "no-services"
    empty.mkdir()
    with (
        patch("frappe_manager.CLI_SERVICES_DIRECTORY", empty),
        patch("frappe_manager.utils.network.detect_running_network", return_value=None),
    ):
        yield


@pytest.fixture
def v0200(output, tmp_path):
    migration = MigrationV0200(output_handler=output)
    migration.benches_dir = tmp_path / "sites"
    migration.backup_manager = BackupManager(
        name="0.20.0",
        benches_dir=tmp_path / "sites",
        backup_dir=tmp_path / "backups",
    )
    migration.migration_executor = _executor()
    return migration


@pytest.fixture
def v0200_bench(tmp_path):
    bench_path = tmp_path / "sites" / "alpha"
    (bench_path / "workspace" / "frappe-bench" / "sites").mkdir(parents=True)
    return _FakeBench("alpha", bench_path)


def _admin_tools(bench: _FakeBench, body: str = _ADMIN_TOOLS_COMPOSE) -> Path:
    path = bench.path / "docker-compose.admin-tools.yml"
    path.write_text(body)
    return path


def _load(path: Path):
    return YAML().load(path.read_text())


def test_a_bench_without_admin_tools_still_gets_the_nginx_work(v0200, v0200_bench, tmp_path):
    services = tmp_path / "services"
    services.mkdir()
    (services / "docker-compose.yml").write_text(
        "networks:\n  global-frontend-network:\n    ipam:\n      config:\n        - subnet: 10.5.0.0/16\n",
    )
    default_conf = v0200_bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
    default_conf.parent.mkdir(parents=True)
    default_conf.write_text("server { }\n")

    with patch("frappe_manager.CLI_SERVICES_DIRECTORY", services):
        v0200.migrate_bench(v0200_bench)

    realip = v0200_bench.path / "configs" / "nginx" / "conf" / "custom" / "real-ip.conf"
    assert "10.5.0.0/16" in realip.read_text()
    assert not default_conf.exists()
    # No admin-tools compose, so no adminer work happened.
    assert not (v0200_bench.path / "configs" / "adminer").exists()


def test_the_admin_tools_compose_is_backed_up_before_being_rewritten(v0200, v0200_bench, no_frontend_subnet):
    compose_path = _admin_tools(v0200_bench)

    v0200.migrate_bench(v0200_bench)

    backup = next(b for b in v0200.backup_manager.backups if b.src == compose_path)
    assert backup.bench == "alpha"
    assert backup.real_dest.read_text() == _ADMIN_TOOLS_COMPOSE
    assert compose_path.read_text() != _ADMIN_TOOLS_COMPOSE


def test_adminer_moves_to_5_and_loses_its_hardcoded_default_server(v0200, v0200_bench, no_frontend_subnet):
    compose_path = _admin_tools(v0200_bench)

    v0200.migrate_bench(v0200_bench)

    data = _load(compose_path)
    assert data["services"]["adminer"]["image"] == "adminer:5"
    # The whole environment block goes when ADMINER_DEFAULT_SERVER was its only key.
    assert "environment" not in data["services"]["adminer"]
    assert data["services"]["adminer"]["restart"] == "always"


def test_an_environment_with_other_keys_survives_without_the_default_server(v0200, v0200_bench, no_frontend_subnet):
    compose_path = _admin_tools(
        v0200_bench,
        _ADMIN_TOOLS_COMPOSE.replace(
            "      ADMINER_DEFAULT_SERVER: global-db\n",
            "      ADMINER_DEFAULT_SERVER: global-db\n      ADMINER_DESIGN: dracula\n",
        ),
    )

    v0200.migrate_bench(v0200_bench)

    environment = _load(compose_path)["services"]["adminer"]["environment"]
    assert dict(environment) == {"ADMINER_DESIGN": "dracula"}


def test_the_old_volumes_are_replaced_by_the_read_only_login_mounts(v0200, v0200_bench, no_frontend_subnet):
    compose_path = _admin_tools(v0200_bench)

    v0200.migrate_bench(v0200_bench)

    assert list(_load(compose_path)["services"]["adminer"]["volumes"]) == ADMINER_VOLUMES


def test_the_compose_records_the_migration_version_without_a_v_prefix(v0200, v0200_bench, no_frontend_subnet):
    compose_path = _admin_tools(v0200_bench)

    v0200.migrate_bench(v0200_bench)

    assert _load(compose_path)["x-version"] == "0.20.0"


def test_the_login_plugin_is_placed_in_the_benchs_adminer_config_dir(v0200, v0200_bench, no_frontend_subnet):
    _admin_tools(v0200_bench)

    v0200.migrate_bench(v0200_bench)

    plugin = v0200_bench.path / "configs" / "adminer" / "000-fm-login.php"
    assert plugin.read_bytes().startswith(b"<?php")


def test_a_compose_without_an_adminer_service_is_left_untouched(v0200, v0200_bench, no_frontend_subnet):
    body = "x-version: '0.19.0'\nservices:\n  something-else:\n    image: busybox\n"
    compose_path = _admin_tools(v0200_bench, body)

    v0200.migrate_bench(v0200_bench)

    assert compose_path.read_text() == body
    assert not (v0200_bench.path / "configs" / "adminer").exists()


def test_the_real_ip_conf_falls_back_to_the_running_network(v0200, v0200_bench, tmp_path):
    empty = tmp_path / "no-services"
    empty.mkdir()

    with (
        patch("frappe_manager.CLI_SERVICES_DIRECTORY", empty),
        patch("frappe_manager.utils.network.detect_running_network", return_value={"subnet_cidr": "10.9.0.0/16"}),
    ):
        v0200._place_realip_conf(v0200_bench)

    conf = v0200_bench.path / "configs" / "nginx" / "conf" / "custom" / "real-ip.conf"
    assert "10.9.0.0/16" in conf.read_text()


def test_no_discoverable_subnet_means_no_real_ip_conf(v0200, v0200_bench, no_frontend_subnet):
    v0200._place_realip_conf(v0200_bench)

    assert not (v0200_bench.path / "configs" / "nginx").exists()


def test_a_failing_network_probe_does_not_abort_the_migration(v0200, v0200_bench, tmp_path):
    empty = tmp_path / "no-services"
    empty.mkdir()

    with (
        patch("frappe_manager.CLI_SERVICES_DIRECTORY", empty),
        patch(
            "frappe_manager.utils.network.detect_running_network",
            side_effect=RuntimeError("no docker"),
        ),
    ):
        v0200._place_realip_conf(v0200_bench)

    assert not (v0200_bench.path / "configs" / "nginx").exists()


def test_the_generated_default_conf_is_backed_up_then_deleted(v0200, v0200_bench):
    default_conf = v0200_bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
    default_conf.parent.mkdir(parents=True)
    default_conf.write_text("server { access_log /dev/stdout; }\n")

    v0200._refresh_nginx_default_conf(v0200_bench)

    assert not default_conf.exists()
    backup = next(b for b in v0200.backup_manager.backups if b.src == default_conf)
    assert backup.real_dest.read_text() == "server { access_log /dev/stdout; }\n"


def test_a_bench_without_a_generated_default_conf_is_left_alone(v0200, v0200_bench):
    v0200._refresh_nginx_default_conf(v0200_bench)

    assert v0200.backup_manager.backups == []


def test_the_old_admin_tools_credentials_move_into_the_auth_table(v0200, v0200_bench):
    config = v0200_bench.path / "bench_config.toml"
    config.write_text('name = "alpha"\nadmin_tools_username = "bob"\nadmin_tools_password = "hunter2"\n')

    v0200._move_admin_tools_credentials(v0200_bench)

    doc = tomlkit.parse(config.read_text())
    assert "admin_tools_username" not in doc
    assert "admin_tools_password" not in doc
    assert dict(doc["auth"]) == {"user": "bob", "password": "hunter2", "web": False, "tools": True}
    assert doc["name"] == "alpha"


def test_an_existing_auth_table_wins_but_the_old_keys_still_go(v0200, v0200_bench):
    config = v0200_bench.path / "bench_config.toml"
    config.write_text(
        'admin_tools_username = "bob"\nadmin_tools_password = "hunter2"\n[auth]\nuser = "newer"\nweb = true\n',
    )

    v0200._move_admin_tools_credentials(v0200_bench)

    doc = tomlkit.parse(config.read_text())
    assert "admin_tools_username" not in doc
    assert dict(doc["auth"]) == {"user": "newer", "web": True}


def test_a_missing_username_becomes_admin_and_a_missing_password_is_omitted(v0200, v0200_bench):
    config = v0200_bench.path / "bench_config.toml"
    config.write_text('admin_tools_password = "hunter2"\n')

    v0200._move_admin_tools_credentials(v0200_bench)

    assert dict(tomlkit.parse(config.read_text())["auth"]) == {
        "user": "admin",
        "password": "hunter2",
        "web": False,
        "tools": True,
    }

    config.write_text('admin_tools_username = "bob"\n')
    v0200._move_admin_tools_credentials(v0200_bench)

    assert dict(tomlkit.parse(config.read_text())["auth"]) == {"user": "bob", "web": False, "tools": True}


def test_the_renamed_admin_tools_htpasswd_is_dropped(v0200, v0200_bench):
    htpasswd = v0200_bench.path / "configs" / "nginx" / "conf" / "http_auth" / "alpha-admin-tools.htpasswd"
    htpasswd.parent.mkdir(parents=True)
    htpasswd.write_text("alpha:hash\n")

    v0200._move_admin_tools_credentials(v0200_bench)

    assert not htpasswd.exists()


def test_keys_removed_in_0_20_0_are_stripped_from_bench_config(v0200, v0200_bench):
    """The models dropped these, so the file must stop carrying them.

    Two shapes: a single key out of a table that survives (`[switch].search_replace`), and a
    whole table (`[registry]`, whose every field existed only to run `docker login`, which
    docker already owns). Neighbouring keys and unrelated tables must survive both.
    """
    config = v0200_bench.path / "bench_config.toml"
    config.write_text(
        'name = "alpha"\n'
        "[switch]\nmigrate = true\nsearch_replace = true\n"
        '[registry]\nregistry = "ghcr.io/acme"\nusername = "u"\npassword = "p"\n',
    )

    v0200._drop_removed_config_keys(v0200_bench)

    doc = tomlkit.parse(config.read_text())
    assert "search_replace" not in doc["switch"]
    assert "registry" not in doc, "the whole table goes, not just its keys"
    assert doc["switch"]["migrate"] is True
    assert doc["name"] == "alpha"


def test_a_config_without_the_removed_keys_is_not_rewritten(v0200, v0200_bench):
    """No key present means no write, so mtimes and formatting are left alone."""
    config = v0200_bench.path / "bench_config.toml"
    original = 'name = "alpha"\n[switch]\nmigrate = true\n'
    config.write_text(original)

    v0200._drop_removed_config_keys(v0200_bench)

    assert config.read_text() == original


def test_migrate_bench_actually_runs_the_key_strip(v0200, v0200_bench):
    """Wiring, not behaviour. The helper is unit-tested directly above, so nothing there
    notices if the call goes missing from ``migrate_bench``. Safe to run in full here:
    with no docker-compose.admin-tools.yml it returns before the compose rewrite."""
    config = v0200_bench.path / "bench_config.toml"
    config.write_text('name = "alpha"\n[switch]\nsearch_replace = true\n')

    v0200.migrate_bench(v0200_bench)

    assert "search_replace" not in tomlkit.parse(config.read_text())["switch"]


def test_a_config_without_the_old_keys_is_not_rewritten(v0200, v0200_bench, output):
    config = v0200_bench.path / "bench_config.toml"
    config.write_text('name = "alpha"\n[auth]\nuser = "bob"\n')

    v0200._move_admin_tools_credentials(v0200_bench)

    assert config.read_text() == 'name = "alpha"\n[auth]\nuser = "bob"\n'
    output.print.assert_not_called()


# --------------------------------------------------------------------------------------
# MigrationV0200: the global database engine upgrade
# --------------------------------------------------------------------------------------


@pytest.fixture
def services(v0200, tmp_path):
    """A services manager whose compose file is a real dict plus mocked docker."""
    compose_path = tmp_path / "services" / "docker-compose.yml"
    compose_path.parent.mkdir(parents=True)
    manager = MagicMock()
    manager.compose_file_manager.exists.return_value = True
    manager.compose_file_manager.compose_path = compose_path
    manager.compose_file_manager.yml = {
        "services": {
            "global-db": {
                "image": "mariadb:10.6",
                "command": ["--character-set-server=utf8mb4", "--skip-innodb-read-only-compressed"],
                "environment": {"MYSQL_ROOT_PASSWORD": "root"},
            },
        },
    }
    v0200.services_manager = manager
    return manager


def _dump_lands_on_host(services):
    """compose cp is what puts the dump on the host; mirror that so the gzip step runs."""

    def _cp(source, dest, stream=False):
        Path(dest).write_text("-- all databases\n")

    services.compose.cp.side_effect = _cp


def test_the_engine_upgrade_is_skipped_without_a_services_compose(v0200, services):
    services.compose_file_manager.exists.return_value = False

    v0200.migrate_services()

    services.compose.stop.assert_not_called()
    services.compose_file_manager.write_to_file.assert_not_called()


@pytest.mark.parametrize(
    "services_block",
    [
        {},
        {"global-db": {}},
        {"other": {"image": "busybox"}},
    ],
)
def test_the_engine_upgrade_is_skipped_when_there_is_no_global_db_image(v0200, services, services_block):
    services.compose_file_manager.yml = {"services": services_block}

    v0200.migrate_services()

    services.compose.stop.assert_not_called()
    services.compose_file_manager.write_to_file.assert_not_called()


def test_an_engine_already_on_the_pinned_image_is_left_alone(v0200, services):
    services.compose_file_manager.yml["services"]["global-db"]["image"] = GLOBAL_DB_IMAGE

    v0200.migrate_services()

    services.compose.stop.assert_not_called()
    services.compose_file_manager.write_to_file.assert_not_called()


def test_the_engine_is_dumped_before_it_is_stopped_rewritten_and_restarted(v0200, services):
    order = []
    _dump_lands_on_host(services)
    services.compose.cp.side_effect = lambda source, dest, stream=False: (
        order.append("cp"),
        Path(dest).write_text("-- all databases\n"),
    )
    services.compose.stop.side_effect = lambda **kwargs: order.append(("stop", kwargs))
    services.compose.up.side_effect = lambda **kwargs: order.append(("up", kwargs))
    services.compose_file_manager.write_to_file.side_effect = lambda: order.append("write")

    with patch(f"{V0200}.MariaDBManager") as manager_cls, patch(f"{V0200}.DatabaseServerServiceInfo"):
        manager_cls.return_value.db_export_all.side_effect = lambda path: order.append("dump")
        manager_cls.return_value.wait_till_db_start.side_effect = lambda: order.append("wait")
        v0200.migrate_services()

    assert [step if isinstance(step, str) else step[0] for step in order] == [
        "dump",
        "cp",
        "stop",
        "write",
        "up",
        "wait",
    ]
    stop_kwargs = next(step[1] for step in order if not isinstance(step, str) and step[0] == "stop")
    assert stop_kwargs == {"services": ["global-db"], "timeout": 120}
    up_kwargs = next(step[1] for step in order if not isinstance(step, str) and step[0] == "up")
    assert up_kwargs == {"services": ["global-db"], "force_recreate": True, "detach": True, "pull": "missing"}


def test_the_upgraded_compose_service_is_rewritten_in_place(v0200, services):
    _dump_lands_on_host(services)

    with patch(f"{V0200}.MariaDBManager"), patch(f"{V0200}.DatabaseServerServiceInfo"):
        v0200.migrate_services()

    engine = services.compose_file_manager.yml["services"]["global-db"]
    assert engine["image"] == GLOBAL_DB_IMAGE
    assert "--skip-innodb-read-only-compressed" not in engine["command"]
    assert engine["environment"]["MARIADB_AUTO_UPGRADE"] == 1
    assert engine["environment"]["MYSQL_ROOT_PASSWORD"] == "root"


def test_the_pre_upgrade_dump_is_copied_out_of_the_container_and_compressed(v0200, services):
    _dump_lands_on_host(services)

    with patch(f"{V0200}.MariaDBManager") as manager_cls, patch(f"{V0200}.DatabaseServerServiceInfo"):
        v0200.migrate_services()

    container_path = manager_cls.return_value.db_export_all.call_args.args[0]
    assert container_path.parent == Path("/tmp")
    source, dest = services.compose.cp.call_args.args
    assert source == f"global-db:{container_path}"
    assert services.compose.cp.call_args.kwargs == {"stream": False}

    dumps = list(v0200.backup_manager.backup_dir.glob("global-db-all-databases-*.sql.gz"))
    assert len(dumps) == 1
    with gzip.open(dumps[0], "rb") as f:
        assert f.read() == b"-- all databases\n"
    # The uncompressed copy is not left behind next to it.
    assert not Path(dest).exists()


# --------------------------------------------------------------------------------------
# Rolling the v0.20.0 migration back
# --------------------------------------------------------------------------------------


def test_undoing_the_services_migration_restores_only_the_services_compose(v0200, services, output):
    compose_path = services.compose_file_manager.compose_path
    compose_path.write_text("original\n")
    v0200.backup_manager.backup(compose_path)
    other = v0200.backup_manager.backup_dir.parent / "unrelated.yml"
    other.write_text("unrelated\n")
    unrelated_backup = v0200.backup_manager.backup(other)
    compose_path.write_text("migrated\n")

    v0200.undo_services_migrate()

    assert compose_path.read_text() == "original\n"
    assert unrelated_backup.is_restored is False
    # The datadir is not rolled back, and the operator is told where the dump is.
    assert "NOT rolled back" in output.warning.call_args.args[0]


def test_undoing_a_bench_restores_the_admin_tools_compose_and_drops_only_the_plugin_file(v0200, v0200_bench):
    """The directory is a bind-mount SOURCE for a container migrate does not stop here; rmtree-ing
    it would strand that container on the inode docker already resolved, which is the defect this
    step exists to not reintroduce. Only the file this migration placed comes back out."""
    compose_path = _admin_tools(v0200_bench)
    v0200.backup_manager.backup(compose_path, bench_name="alpha")
    compose_path.write_text("migrated\n")
    plugin_dir = v0200_bench.path / "configs" / "adminer"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "000-fm-login.php").write_text("<?php")
    (plugin_dir / "other-plugin.php").write_text("<?php // not fm's to remove")

    v0200.undo_bench_migrate(v0200_bench)

    assert compose_path.read_text() == _ADMIN_TOOLS_COMPOSE
    assert not (plugin_dir / "000-fm-login.php").exists()
    assert plugin_dir.exists()
    assert (plugin_dir / "other-plugin.php").read_text() == "<?php // not fm's to remove"


def test_undoing_a_bench_that_was_never_backed_up_is_harmless(v0200, v0200_bench):
    v0200.undo_bench_migrate(v0200_bench)

    assert not (v0200_bench.path / "docker-compose.admin-tools.yml").exists()
