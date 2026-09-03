"""The destructive-path guards from the design's "## Guards" section.

Things fm must never do on a database it does not own, each asserted at the seam that would do
it. The refusals are policy, not incapacity: the site's own grant carries `DROP` at schema scope
and its password sits in `site_config.json` where fm reads it routinely, so nothing except these
guards stands between `fm delete` and someone's production schema.

The `--force`/`--no-setup-db` pairing and the root password's absence from the external `new-site`
argv are covered in `test_bench_site_force.py`; this file covers only what that one does not --
the forced (image-runtime) external invocation, and the commands that follow `new-site`.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig, DeployState, DeployStateEntry
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager
from frappe_manager.site_manager.site import Bench

GLOBAL_DB_SITE = "local.localhost"
EXTERNAL_SITE = "app.example.com"
# The bench holding that site, deliberately NOT the same string. `[database]` is keyed by SITE
# (`bench_config.py:1374`, `self.database.get(site or self.name)`) and the toml's `name` is the
# bench, so a fixture that uses one string for both cannot tell a site-keyed lookup from a
# bench-keyed one: the guard would still fire if the code read the wrong identity. Keeping them
# distinct is what makes these tests fail when the `[database]` lookup stops being site-keyed.
EXTERNAL_BENCH = "shop"
EXTERNAL_HOST = "mydb.abc.rds.amazonaws.com"
SCHEMA = "app_prod"
GLOBAL_SCHEMA = "fm_local_localhost_a1b2"  # what fm mints for a site of its own: `fm_<site>_<hex>`
ROOT_PASSWORD = "global-db-root-secret"


def _config(tmp_path: Path, *, name: str, external_site: str | None = None, ca: str | None = None) -> BenchConfig:
    """A bench config recording its own site, plus `external_site` on an external database.

    Every site the config describes gets a `[sites."<site>"]` entry, because that is now the
    invariant: create and the migration both write one per bench, and it is how a bench-scoped
    command knows which site it means. `external_site` differs from `name` in the test that proves
    the guard resolves per site rather than per bench, and there the config legitimately describes
    two: the bench's own on global-db, and the external one.
    """
    toml = f'name = "{name}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
    toml += f'\n[sites."{name}"]\n'
    if external_site and external_site != name:
        toml += f'\n[sites."{external_site}"]\n'
    if external_site:
        toml += f'\n[sites."{external_site}".database]\nhost = "{EXTERNAL_HOST}"\nname = "{SCHEMA}"\n'
        if ca:
            toml += f'ca = "{ca}"\n'
    path = tmp_path / f"{name}.toml"
    path.write_text(toml)
    return BenchConfig.import_from_toml(path)


def _site_on_disk(bench_path: Path, site: str, schema: str) -> None:
    """Write `sites/<site>/site_config.json`, the file `Bench.site_schemas()` enumerates.

    The enumeration is from DISK rather than from `bench_config.toml`, because that file is the
    only record of the schema fm minted for a site and because delete has to work on a bench whose
    config is stale or missing. A bench with no site directory therefore holds no sites at all.
    """
    site_dir = bench_path / "workspace" / "frappe-bench" / "sites" / site
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": schema}))


def _bench(tmp_path: Path, config: BenchConfig, name: str, sites: dict[str, str]) -> Bench:
    """A stand-in bench called `name`, serving `sites` (site name -> its `db_name`) on disk."""
    bench = Bench.__new__(Bench)  # bypass __init__: no Docker, no compose, no services
    bench.name = name
    bench.path = tmp_path / name
    bench.bench_config = config
    bench.logger = MagicMock()
    bench.output = MagicMock()
    # `remove_bench` clears the proxy's per-domain vhost.d entries (upload limit, HSTS) for every
    # domain the bench serves; `services.path` is what locates that (nonexistent here, so both
    # clears are no-ops) directory. Real `Path`, not a `MagicMock`, so `/ "nginx-proxy" / "vhostd"`
    # and `.exists()` behave like the genuine attribute this stands in for.
    bench.services = MagicMock()
    bench.services.path = tmp_path / "services"
    for site, schema in sites.items():
        _site_on_disk(bench.path, site, schema)
    # The real drop path: Bench.remove_database_and_user() delegates here. Asserting on this
    # rather than on the Bench method keeps the whole chain under test.
    bench.database = MagicMock()
    # The other two steps of the removal sequence, stubbed: these tests are about which schemas it
    # will and will not drop, and the sequence reaches them either side of that decision.
    bench.remove_certificate = MagicMock()  # type: ignore[method-assign]
    bench.remove_containers_and_dirs = MagicMock()  # type: ignore[method-assign]
    return bench


def _dropped(bench: Bench) -> list[str]:
    """The sites whose schema was dropped, in order. Keyed by site, which is the whole point."""
    return [c.args[0] for c in bench.database.remove_database_and_user.call_args_list]


def _printed(output: MagicMock) -> str:
    return "\n".join(str(call.args[0]) for call in output.print.call_args_list if call.args)


# --------------------------------------------------------------------------- fm delete


@pytest.mark.parametrize("preference", [None, True, False])
def test_delete_never_drops_an_external_schema(tmp_path, preference):
    """Not even when the operator passed --delete-db-from-global-db: it is not fm's schema."""
    bench = _bench(
        tmp_path,
        _config(tmp_path, name=EXTERNAL_BENCH, external_site=EXTERNAL_SITE),
        EXTERNAL_BENCH,
        {EXTERNAL_SITE: SCHEMA},
    )

    bench._handle_database_deletion(preference)

    assert bench.database.remove_database_and_user.called is False
    assert bench.output.prompt_ask.called is False  # no prompt either: there is nothing to decide
    message = _printed(bench.output)
    assert EXTERNAL_HOST in message  # the operator has to be told where the data was left
    assert SCHEMA in message


def test_delete_prompts_and_drops_on_global_db(tmp_path):
    """Unchanged behaviour for a bench on the container fm owns."""
    bench = _bench(tmp_path, _config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE, {GLOBAL_DB_SITE: GLOBAL_SCHEMA})
    bench.output.prompt_ask.return_value = "yes"

    bench._handle_database_deletion(None)

    assert bench.output.prompt_ask.call_count == 1
    assert _dropped(bench) == [GLOBAL_DB_SITE]


@pytest.mark.parametrize(("preference", "dropped"), [(True, [GLOBAL_DB_SITE]), (False, [])])
def test_delete_honours_an_explicit_preference_on_global_db(tmp_path, preference, dropped):
    bench = _bench(tmp_path, _config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE, {GLOBAL_DB_SITE: GLOBAL_SCHEMA})

    bench._handle_database_deletion(preference)

    assert bench.output.prompt_ask.called is False
    assert _dropped(bench) == dropped


def test_the_guard_resolves_per_site_not_per_bench(tmp_path):
    """One bench, two sites on disk: the `global-db` one is dropped, the external one is refused.

    The switch is the presence of *that site's own* `[database]` entry. A bench-level test
    (`if config.database:`) would refuse both and quietly leak a `global-db` schema on every
    delete; the mirror bug drops the external one. One pass over one bench is what makes the two
    outcomes comparable: they are decisions the same loop takes about different sites.
    """
    config = _config(tmp_path, name=GLOBAL_DB_SITE, external_site=EXTERNAL_SITE)
    bench = _bench(tmp_path, config, GLOBAL_DB_SITE, {GLOBAL_DB_SITE: GLOBAL_SCHEMA, EXTERNAL_SITE: SCHEMA})
    bench.output.prompt_ask.return_value = "yes"

    bench._handle_database_deletion(None)

    assert _dropped(bench) == [GLOBAL_DB_SITE]
    assert bench.output.prompt_ask.call_count == 1  # the external site is never asked about
    assert EXTERNAL_HOST in _printed(bench.output)


def _service(output: MagicMock, bench: Bench) -> BenchService:
    service = BenchService.__new__(BenchService)  # bypass __init__: no docker client, no services
    service.output = output
    service.get_bench = MagicMock(return_value=bench)  # type: ignore[method-assign]
    return service


def test_bench_service_delete_shares_the_guard(tmp_path):
    """`fm delete` goes through `BenchService`, which must refuse identically.

    It cannot differ any more: `BenchService` carried its own copy of this sequence for the `--yes`
    path, the two drifted in the wording of the database question, and the copy is now gone. Asserted
    through the public entry point rather than the handler, so it stays true however the delegation
    is spelled.

    A mixed bench, because that is what this config describes: the bench's own site on fm's global-db
    and a second one on a server fm does not own. Both are recorded in `[sites]`, so both have to be
    on disk; a recorded site with no `site_config.json` is unreadable, and unreadable blocks.
    """
    output = MagicMock()
    bench = _bench(
        tmp_path,
        _config(tmp_path, name=EXTERNAL_BENCH, external_site=EXTERNAL_SITE),
        EXTERNAL_BENCH,
        {EXTERNAL_BENCH: GLOBAL_SCHEMA, EXTERNAL_SITE: SCHEMA},
    )
    bench.output.prompt_ask.return_value = "yes"

    _service(output, bench).delete_bench(EXTERNAL_BENCH, yes=True)

    # The external one is never dropped and never asked about; the global-db one still is.
    assert _dropped(bench) == [EXTERNAL_BENCH]
    assert EXTERNAL_SITE not in _dropped(bench)
    assert EXTERNAL_HOST in _printed(bench.output)
    # Resolved, not outstanding: a schema fm does not own does not block the directory.
    bench.remove_containers_and_dirs.assert_called_once_with()


def test_bench_service_delete_still_drops_a_global_db_schema(tmp_path):
    bench = _bench(tmp_path, _config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE, {GLOBAL_DB_SITE: GLOBAL_SCHEMA})
    bench.output.prompt_ask.return_value = "yes"

    _service(MagicMock(), bench).delete_bench(GLOBAL_DB_SITE, yes=True)

    assert bench.output.prompt_ask.call_count == 1
    assert _dropped(bench) == [GLOBAL_DB_SITE]


def test_the_yes_flag_skips_only_the_removal_confirmation(tmp_path):
    """`--yes` means "do not ask whether to remove the bench". It does NOT mean "drop the schema":
    that question is separate and `--delete-db-from-global-db` answers it, so one prompt remains."""
    bench = _bench(tmp_path, _config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE, {GLOBAL_DB_SITE: GLOBAL_SCHEMA})
    bench.output.prompt_ask.return_value = "no"

    _service(MagicMock(), bench).delete_bench(GLOBAL_DB_SITE, yes=True)

    asked = [str(call.kwargs.get("prompt", "")) for call in bench.output.prompt_ask.call_args_list]
    assert len(asked) == 1
    # Both prompts contain "want to remove", so the schema question is what names a database.
    assert "global-db" in asked[0]
    assert "the database" in asked[0]
    assert GLOBAL_DB_SITE in asked[0]  # and it names the SITE whose schema is at stake


def test_without_the_yes_flag_the_removal_is_confirmed_first(tmp_path):
    """And answering no removes nothing at all, including the schema."""
    bench = _bench(tmp_path, _config(tmp_path, name=GLOBAL_DB_SITE), GLOBAL_DB_SITE, {GLOBAL_DB_SITE: GLOBAL_SCHEMA})
    bench.output.prompt_ask.return_value = "no"

    assert _service(MagicMock(), bench).delete_bench(GLOBAL_DB_SITE, yes=False) is False
    assert bench.database.remove_database_and_user.called is False


# --------------------------------------------------------------------------- common_site_config


def test_common_site_config_carries_no_endpoint_key_at_all(tmp_path):
    """Not `db_host`, not `db_port`, and above all not `db_ssl_*`.

    `common_site_config.json` is bench-wide. A sibling site on another server reads these keys
    too, and a `db_ssl_ca` there was measured breaking a `global-db` sibling outright: it began
    failing with `TLS/SSL error: self-signed certificate` for as long as the key was present.
    """
    config = _config(tmp_path, name=EXTERNAL_BENCH, external_site=EXTERNAL_SITE, ca="/host/rds-bundle.pem")
    # The keys do exist -- in the per-site file, which is where they belong.
    site_config = config.get_site_config_data(EXTERNAL_SITE)
    assert site_config["db_host"] == EXTERNAL_HOST
    assert "db_ssl_ca" in site_config

    common = config.get_commmon_site_config_data()

    assert [key for key in common if key.startswith("db_")] == []
    serialized = json.dumps(common)
    assert EXTERNAL_HOST not in serialized
    assert "db-ca.pem" not in serialized


# --------------------------------------------------------------------------- new-site argv


def _site_manager(captured: list[tuple[str, dict]], config: BenchConfig) -> BenchSiteManager:
    manager = object.__new__(BenchSiteManager)  # bypass __init__ (no Docker/services setup)
    manager.bench_name = config.name
    manager.bench_cli_cmd = ["bench"]
    manager.bench_config = config
    manager.output = MagicMock()
    info = manager.services = MagicMock()
    info.database_manager.database_server_info.password = ROOT_PASSWORD
    info.database_manager.database_server_info.host = "global-db"
    info.database_manager.database_server_info.port = 3306

    def run(command, **kwargs):
        captured.append((command, kwargs))

    manager._container_run = run  # type: ignore[method-assign]
    return manager


def test_no_global_db_secret_or_endpoint_reaches_an_external_create(tmp_path):
    """Every command of the external create, not just `new-site`, plus the env they carry.

    `test_bench_site_force.py` asserts the root password is absent from the `new-site` argv; the
    `bench use` and `scheduler enable` calls that follow it are the part not covered there, and
    they take the same env.
    """
    captured: list[tuple[str, dict]] = []
    # `config.name` is the SITE here, not the bench: `create_bench_site` creates the site the
    # config names, and the external branch triggers on `[sites."<that site>".database]`. Splitting the
    # two the way the delete guards above do would describe a bench holding a `[database]` entry
    # for a site it is not creating, which is a misconfiguration rather than this scenario. It was
    # measured: with the names split, the argv came out as global-db, carrying the root password
    # and no `--no-setup-db`. That is the phase 3 failure mode, and it belongs in a test of its
    # own once a site has an identity separate from the bench.
    config = _config(tmp_path, name=EXTERNAL_SITE, external_site=EXTERNAL_SITE)

    _site_manager(captured, config).create_bench_site()

    assert captured, "the external create issued no commands at all"
    for command, kwargs in captured:
        assert ROOT_PASSWORD not in command
        assert "global-db" not in command  # the endpoint comes from site_config.json, not the argv
        assert ROOT_PASSWORD not in json.dumps(kwargs.get("env") or {})

    new_site_command = captured[0]
    assert "new-site" in new_site_command[0]
    # The endpoint flags belong to the global-db branch: on the external path Frappe reads host,
    # port and TLS out of the site file, which the create pipeline wrote before this ran.
    for flag in ("--db-host", "--db-port", "--db-name", "--db-root-username", "--db-root-password"):
        assert flag not in new_site_command[0]


def test_external_create_pairs_no_setup_db_with_force_even_when_forced(tmp_path):
    """The image runtime reaches `create_bench_site(force=True)`; the pairing must still hold.

    `--force` alone is inert, because `force` reaches only `setup_database` and `--no-setup-db`
    turns that off. The two together are what drops a schema, and this is the one call site that
    asks for `--force` explicitly rather than getting it from the external branch.
    """
    captured: list[tuple[str, dict]] = []
    config = _config(tmp_path, name=EXTERNAL_SITE, external_site=EXTERNAL_SITE)  # the site, per the note above

    _site_manager(captured, config).create_bench_site(force=True)

    new_site_command = captured[0][0]
    assert "--force" in new_site_command
    assert "--no-setup-db" in new_site_command
    assert new_site_command.count("--force") == 1  # the external branch must not force twice over


# ------------------------------- a recorded site with no site config


"""ABSENT is not UNREADABLE, and the difference decides whether the record can ever be cleared.

`unreadable` blocks removal because the file that will not parse may hold the only record of a
schema still sitting in global-db, and destroying it makes that schema findable only by hand. That
reasoning does not survive the file being gone: there is no record to lose and no directory to
keep, so blocking bought nothing and made a `[sites]` entry with no site permanently unremovable.
`fm info` reports that entry as missing; without this, nothing could act on the report.

fm mints a schema as `fm_<site>_<hex>` and writes the name ONLY into the site config, so when it
is gone the name is no longer knowable. Hence a warning rather than silence.
"""


def _phantom_config(tmp_path, bench, *extra_sites):
    """A config recording the bench's own site plus `extra_sites`, none of them external."""
    toml = f'name = "{bench}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
    for site in extra_sites:
        toml += f'\n[sites."{site}"]\n'
    path = tmp_path / f"{bench}.toml"
    path.write_text(toml)
    return BenchConfig.import_from_toml(path)


def test_site_schemas_marks_a_recorded_site_with_no_config_as_absent(tmp_path):
    config = _phantom_config(tmp_path, "shop", "real.localhost", "ghost.localhost")
    bench = _bench(tmp_path, config, "shop", {"real.localhost": "fm_real_a1"})

    by_site = {e.site: e for e in bench.site_schemas()}

    assert by_site["ghost.localhost"].absent is True
    assert by_site["real.localhost"].absent is False
    # ABSENT does not report as unreadable, which is what used to block removal.
    assert by_site["ghost.localhost"].unreadable is False


def test_a_config_present_but_unparseable_is_unreadable_not_absent(tmp_path):
    config = _phantom_config(tmp_path, "shop", "broken.localhost")
    bench = _bench(tmp_path, config, "shop", {})
    site_dir = bench.path / "workspace" / "frappe-bench" / "sites" / "broken.localhost"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text("{not json")

    entry = {e.site: e for e in bench.site_schemas()}["broken.localhost"]

    assert entry.absent is False
    assert entry.unreadable is True


def test_a_config_present_with_no_db_name_is_unreadable_not_absent(tmp_path):
    config = _phantom_config(tmp_path, "shop", "nodb.localhost")
    bench = _bench(tmp_path, config, "shop", {})
    site_dir = bench.path / "workspace" / "frappe-bench" / "sites" / "nodb.localhost"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text("{}")

    entry = {e.site: e for e in bench.site_schemas()}["nodb.localhost"]

    assert entry.absent is False
    assert entry.unreadable is True


def test_an_absent_site_resolves_instead_of_blocking(tmp_path):
    config = _phantom_config(tmp_path, "shop", "ghost.localhost")
    bench = _bench(tmp_path, config, "shop", {})
    entry = {e.site: e for e in bench.site_schemas()}["ghost.localhost"]

    # None means resolved: nothing outstanding, so removal may proceed.
    assert bench._resolve_site_schema(entry, delete_db_from_global_db=True) is None
    assert _dropped(bench) == []


def test_the_absent_warning_says_the_schema_may_still_be_there(tmp_path):
    # The operator has to be told, because fm writes the minted name ONLY into the site config,
    # so once that is gone the name is no longer recoverable from disk.
    config = _phantom_config(tmp_path, "shop", "ghost.localhost")
    bench = _bench(tmp_path, config, "shop", {})
    entry = {e.site: e for e in bench.site_schemas()}["ghost.localhost"]

    bench._resolve_site_schema(entry, delete_db_from_global_db=True)

    warned = "\n".join(str(c.args[0]) for c in bench.output.warning.call_args_list if c.args)
    assert "ghost.localhost" in warned
    assert "no site_config.json" in warned
    assert "global-db" in warned
    assert "fm_" in warned


def test_an_unreadable_site_still_blocks(tmp_path):
    # The half this must NOT relax: the file is there, so it is the record, and it does not answer.
    config = _phantom_config(tmp_path, "shop", "broken.localhost")
    bench = _bench(tmp_path, config, "shop", {})
    site_dir = bench.path / "workspace" / "frappe-bench" / "sites" / "broken.localhost"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text("{not json")
    entry = {e.site: e for e in bench.site_schemas()}["broken.localhost"]

    why = bench._resolve_site_schema(entry, delete_db_from_global_db=True)

    assert why is not None
    assert "could not be read" in why


# --------------------------------------------------------------- what removal leaves behind


"""Removing a site used to leave three pieces of its state on disk and in the config.

Each was invisible until something else tripped over it: a `vhost.d/<domain>` upload-limit file
outlived the site and a domain later pointed at another bench inherited a stale
`client_max_body_size`; a `deploy_state.history[*].backups` row kept naming the site, and rollback
iterates that map per site, so a restore aimed at a schema that no longer exists; and the external
database's `config/tls/<site>` material simply stayed.
"""


def _removable(tmp_path: Path, sites: dict[str, str]):
    """A bench whose removal reaches the cleanup, with the destructive steps stubbed."""
    config = _config(tmp_path, name="shop.localhost", external_site="b.example.com")
    bench = _bench(tmp_path, config, "shop", sites)
    bench.ssl = MagicMock()
    bench.services = MagicMock()
    bench.services.path = tmp_path / "services"
    bench.save_bench_config = MagicMock()  # type: ignore[method-assign]
    bench.republish_site_map = MagicMock()  # type: ignore[method-assign]
    (bench.services.path / "nginx-proxy" / "vhostd").mkdir(parents=True)
    return bench


def _vhostd(bench) -> Path:
    return bench.services.path / "nginx-proxy" / "vhostd"


def test_the_removed_sites_proxy_upload_limit_files_go(tmp_path):
    bench = _removable(tmp_path, {"shop.localhost": "s1", "b.example.com": "s2"})
    for domain in ("shop.localhost", "b.example.com"):
        (_vhostd(bench) / domain).write_text("client_max_body_size 50m;\n")

    bench.remove_site("b.example.com", delete_db_from_global_db=True)

    assert not (_vhostd(bench) / "b.example.com").exists()


def test_a_surviving_sites_proxy_file_is_untouched(tmp_path):
    """The bench keeps serving its other sites, so their limits must survive."""
    bench = _removable(tmp_path, {"shop.localhost": "s1", "b.example.com": "s2"})
    for domain in ("shop.localhost", "b.example.com"):
        (_vhostd(bench) / domain).write_text("client_max_body_size 50m;\n")

    bench.remove_site("b.example.com", delete_db_from_global_db=True)

    assert (_vhostd(bench) / "shop.localhost").read_text() == "client_max_body_size 50m;\n"


def test_the_removed_sites_backup_rows_are_dropped_but_the_dumps_are_kept(tmp_path):
    """Default: the row goes so rollback cannot aim at it, the file stays because a dump is the
    last copy of something. The path is printed, since prune can no longer see it."""
    bench = _removable(tmp_path, {"shop.localhost": "s1", "b.example.com": "s2"})
    dump = tmp_path / "b.sql"
    dump.write_text("dump")
    bench.bench_config.deploy_state = DeployState(
        history=[DeployStateEntry(tag="v1", deployed_at="now", migrate_status="migrated",
                                  backups={"shop.localhost": str(tmp_path / "s.sql"), "b.example.com": str(dump)})]
    )

    bench.remove_site("b.example.com", delete_db_from_global_db=True)

    assert bench.bench_config.deploy_state.history[0].backups == {"shop.localhost": str(tmp_path / "s.sql")}
    assert dump.exists()
    warned = "\n".join(str(c.args[0]) for c in bench.output.warning.call_args_list if c.args)
    assert str(dump) in warned


def test_the_dumps_go_when_asked(tmp_path):
    bench = _removable(tmp_path, {"shop.localhost": "s1", "b.example.com": "s2"})
    dump = tmp_path / "b.sql"
    dump.write_text("dump")
    bench.bench_config.deploy_state = DeployState(
        history=[DeployStateEntry(tag="v1", deployed_at="now", migrate_status="migrated",
                                  backups={"b.example.com": str(dump)})]
    )

    bench.remove_site("b.example.com", delete_db_from_global_db=True, delete_backups=True)

    assert not dump.exists()


def test_a_dump_another_release_still_names_survives_being_asked(tmp_path):
    """Same rule prune uses. Two history rows can name one file, and deleting it for the removed
    site would break the release that still holds it."""
    bench = _removable(tmp_path, {"shop.localhost": "s1", "b.example.com": "s2"})
    shared = tmp_path / "shared.sql"
    shared.write_text("dump")
    bench.bench_config.deploy_state = DeployState(
        history=[
            DeployStateEntry(tag="v1", deployed_at="now", migrate_status="migrated",
                             backups={"b.example.com": str(shared)}),
            DeployStateEntry(tag="v2", deployed_at="now", migrate_status="migrated",
                             backups={"shop.localhost": str(shared)}),
        ]
    )

    bench.remove_site("b.example.com", delete_db_from_global_db=True, delete_backups=True)

    assert shared.exists()


def test_the_removed_sites_database_tls_material_goes(tmp_path):
    bench = _removable(tmp_path, {"shop.localhost": "s1", "b.example.com": "s2"})
    # The real location, per db_tls.tls_host_root: inside the mounted workspace, not beside it.
    tls = bench.path / "workspace" / "frappe-bench" / "config" / "tls" / "b.example.com"
    tls.mkdir(parents=True)
    (tls / "db-ca.pem").write_text("cert")

    bench.remove_site("b.example.com", delete_db_from_global_db=True)

    assert not tls.exists()


def test_cleanup_that_fails_warns_and_still_finishes_the_removal(tmp_path):
    """The schema and the directory are already gone by then, so aborting would leave the site
    half-removed AND still recorded, which is worse than a leftover file."""
    bench = _removable(tmp_path, {"shop.localhost": "s1", "b.example.com": "s2"})
    with patch("frappe_manager.site_manager.site.remove_site_tls", side_effect=RuntimeError("permission denied")):
        assert bench.remove_site("b.example.com", delete_db_from_global_db=True) is True

    assert "b.example.com" not in bench.bench_config.sites
    warned = "\n".join(str(c.args[0]) for c in bench.output.warning.call_args_list if c.args)
    assert "permission denied" in warned
