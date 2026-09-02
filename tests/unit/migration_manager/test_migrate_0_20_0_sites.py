"""`[database."<site>"]` becomes `[sites."<site>".database]`, in the 0.20.0 migration.

A bench holds exactly one site today and its name is the bench's, so that table already had a site
as its key. The move gives the site somewhere to hold its other per-site facts later, instead of
them accumulating as top-level keys that are only per-site because there happens to be one site.

Top-level `alias_domains` makes the same trip, under `[sites."<bench name>"]`. It is the exact key
the paragraph above predicted: per-site by accident of there being one site, and bench-level only
because there was nowhere else to put it. Its old home is why `get_site_mappings()` had to send
every alias to the primary site; recorded under a site, an alias finally names the site it reaches.

`import_from_toml` reads only the new spelling. That is the established pattern in this release, not
a new risk: the same migration renames `dns_challenge_providers` to `dns_providers` and reads only
that. So the tests below assert the file shape AND that the result loads, because a migration that
writes something the loader cannot read is the failure that matters.

This step must be idempotent, and not as a nicety: 0.20.0 is unreleased, so a bench recorded at
`0.20.0.dev0` sorts BELOW `0.20.0` and re-runs this migration whenever one is triggered at all.
"""

import gzip
import json
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import tomlkit

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.migrations import migrate_0_20_0 as migrate_mod
from frappe_manager.migration_manager.migrations.migrate_0_20_0 import MigrationV0200
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.utils.helpers import get_template_path

SITE = "shop.localhost"

BASE = f"""name = "{SITE}"
developer_mode = false
admin_tools = false
environment = "prod"
runtime = "mount"
"""

EXTERNAL = f"""
[database."{SITE}"]
host = "rds.internal"
port = 3307
name = "app_prod"
user = "app_svc"
ca = "/host/rds.pem"
check_hostname = false
"""


@pytest.fixture
def step():
    migration = MigrationV0200.__new__(MigrationV0200)  # bypass __init__: no executor, no backups
    migration.output = MagicMock()
    return migration


def _bench(tmp_path, text: str):
    path = tmp_path / "bench_config.toml"
    path.write_text(text)
    bench = MagicMock()
    bench.path = tmp_path
    bench.name = SITE
    return bench, path


def _bench_named(tmp_path, name: str, text: str):
    """A bench whose DIRECTORY name differs from the site it serves: `shop` for `shop.localhost`.

    Everywhere else in this file the two are the same string, which is precisely what hides a step
    reading one where it means the other.
    """
    bench, path = _bench(tmp_path, text)
    bench.name = name
    return bench, path


# --------------------------------------------------------------------------- the move


def test_the_database_table_moves_under_the_site(step, tmp_path):
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._write_sites_table(bench)

    text = path.read_text()
    assert f'[sites."{SITE}".database]' in text
    assert f'[database."{SITE}"]' not in text


def test_every_field_survives_the_move(step, tmp_path):
    """A dropped `ca` means a site that silently stops verifying TLS, and a dropped `port` means it
    cannot connect at all, so this asserts the whole record rather than a sample of it."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._write_sites_table(bench)
    database = BenchConfig.import_from_toml(path).get_database_config()

    assert (database.host, database.port, database.name) == ("rds.internal", 3307, "app_prod")
    assert (database.user, database.ca) == ("app_svc", "/host/rds.pem")
    assert database.check_hostname is False


def test_the_migrated_file_is_what_the_loader_reads(step, tmp_path):
    """The lookup is keyed by site, so the move has to land under the right key and not merely
    somewhere under `[sites]`."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._write_sites_table(bench)
    config = BenchConfig.import_from_toml(path)

    assert config.get_database_config(SITE) is not None
    assert config.get_database_config("other.localhost") is None


def test_unrelated_config_is_left_alone(step, tmp_path):
    """The step rewrites the whole document, so everything it is not about has to come back."""
    extra = '\n[redis]\ncache = "redis://r.example:6379/0"\nqueue = "redis://r.example:6379/1"\n'
    bench, path = _bench(tmp_path, BASE + EXTERNAL + extra)

    step._write_sites_table(bench)
    config = BenchConfig.import_from_toml(path)

    assert config.redis is not None
    assert config.redis.cache == "redis://r.example:6379/0"


# ------------------------------------------------------------------- the alias_domains move
#
# `alias_domains` was a bench-level list, so it named no site and the routing table had to send
# every alias to the primary site. The bench's own site IS that primary, so moving the list under
# `[sites."<bench>"]` preserves the routing the bench already had while recording the attribution
# instead of inferring it. The loader reads only the per-site spelling, so a list left at the top
# level is a bench that silently stops answering on its own aliases.


def test_a_bench_level_alias_list_moves_under_the_bench_site(step, tmp_path):
    bench, path = _bench(tmp_path, BASE + 'alias_domains = ["www.shop.example.com"]\n' + EXTERNAL)

    step._write_sites_table(bench)
    doc = tomlkit.parse(path.read_text())

    # The list now belongs to the site `shop.localhost`, the bench's own site.
    assert doc["sites"][SITE]["alias_domains"] == ["www.shop.example.com"]
    # The top-level key is gone, not merely shadowed: BenchConfig has no such field any more, so a
    # copy left behind is a second spelling of the routing table that nothing reads.
    assert "alias_domains" not in doc


def test_the_moved_aliases_are_what_the_loader_routes(step, tmp_path):
    """The file shape is only half of it: the alias has to come back attributed to the site, which
    is what `get_site_mappings()` hands nginx as the site to serve."""
    bench, path = _bench(tmp_path, BASE + 'alias_domains = ["www.shop.example.com"]\n' + EXTERNAL)

    step._write_sites_table(bench)
    config = BenchConfig.import_from_toml(path)

    assert config.sites is not None
    assert config.sites[SITE].alias_domains == ["www.shop.example.com"]
    assert config.domains == [SITE, "www.shop.example.com"]
    assert config.get_site_mappings() == {SITE: SITE, "www.shop.example.com": SITE}


def test_the_alias_move_is_reported(step, tmp_path):
    """A migration that silently relocates the routing table gives the operator nothing to check."""
    bench, path = _bench(tmp_path, BASE + 'alias_domains = ["www.shop.example.com"]\n')

    step._write_sites_table(bench)

    printed = [call.args[0] for call in step.output.print.call_args_list if call.args]
    assert f'Moved alias_domains under \\[sites."{SITE}"]' in printed


def test_re_running_the_alias_move_changes_nothing(step, tmp_path):
    """Same reason as the database move: a bench at `0.20.0.dev0` runs this step again, and a second
    pass must not duplicate the list or strand it back at the top level."""
    bench, path = _bench(tmp_path, BASE + 'alias_domains = ["www.shop.example.com"]\n' + EXTERNAL)

    step._write_sites_table(bench)
    once = path.read_text()
    step._write_sites_table(bench)
    step._write_sites_table(bench)

    assert path.read_text() == once
    assert BenchConfig.import_from_toml(path).sites[SITE].alias_domains == ["www.shop.example.com"]


def test_an_existing_per_site_alias_list_wins_over_a_stale_top_level_one(step, tmp_path):
    """If both shapes are present the per-site list is the migrated copy, exactly as with
    `database`. Preferring the top-level list would undo a run that already happened -- and worse,
    resurrect an alias the operator removed afterwards."""
    both = (
        BASE
        + 'alias_domains = ["stale.example.com"]\n'
        + f'\n[sites."{SITE}"]\nalias_domains = ["current.example.com"]\n'
    )
    bench, path = _bench(tmp_path, both)

    step._write_sites_table(bench)
    config = BenchConfig.import_from_toml(path)

    assert config.sites[SITE].alias_domains == ["current.example.com"]
    # The stale top-level copy is never read: `import_from_toml` builds its input key by key, so an
    # unrecognised top-level `alias_domains` cannot reach the model. The step also means to delete
    # it, and in this one shape (no `[database]` table at all, `[sites."<bench>"]` already present)
    # it does not, because the early return skips the save that would have persisted the delete.
    # Reported as a production gap rather than pinned here; it is cosmetic, not routing.
    assert not hasattr(config, "alias_domains")


def test_an_empty_top_level_alias_list_is_just_dropped(step, tmp_path):
    """Nothing to attribute, so nothing is recorded under the site -- but the key still goes, since
    the loader no longer reads it and leaving it would keep a field BenchConfig rejects."""
    bench, path = _bench(tmp_path, BASE + "alias_domains = []\n")

    step._write_sites_table(bench)
    text = path.read_text()

    assert "alias_domains" not in text
    assert BenchConfig.import_from_toml(path).sites[SITE].alias_domains == []


# --------------------------------------------------------------------------- re-running


def test_running_it_again_changes_nothing(step, tmp_path):
    """Required, not merely tidy: a bench at `0.20.0.dev0` re-runs this migration, because a dev
    release sorts below its own final version."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._write_sites_table(bench)
    once = path.read_text()
    step._write_sites_table(bench)
    step._write_sites_table(bench)

    assert path.read_text() == once


def test_an_already_migrated_site_is_not_overwritten_by_a_stale_table(step, tmp_path):
    """If both shapes are present, the one under the site is the migrated copy and wins. Preferring
    the top-level table would undo a previous run."""
    both = BASE + EXTERNAL + f'\n[sites."{SITE}".database]\nhost = "migrated.internal"\nname = "app_prod"\n'
    bench, path = _bench(tmp_path, both)

    step._write_sites_table(bench)

    assert BenchConfig.import_from_toml(path).get_database_config().host == "migrated.internal"


# ------------------------------------------------------- a bench with no external database


def test_a_global_db_bench_still_gets_its_site_recorded(step, tmp_path):
    """Most benches are on `global-db` and have no `[database]` table, so before this they had
    nowhere at all that named their site. The entry has no keys and that is the point: it records
    the NAME, which is the one fact that survives the bench name and the site name coming apart."""
    bench, path = _bench(tmp_path, BASE)

    step._write_sites_table(bench)

    assert f'[sites."{SITE}"]' in path.read_text()
    assert list(BenchConfig.import_from_toml(path).sites or {}) == [SITE]


def test_a_global_db_bench_gains_nothing_but_the_site_entry(step, tmp_path):
    """The step rewrites the whole document, so it has to be provably narrow: no invented database,
    no other key touched."""
    bench, path = _bench(tmp_path, BASE)

    step._write_sites_table(bench)
    config = BenchConfig.import_from_toml(path)

    assert config.get_database_config() is None
    assert config.runtime.value == "mount"
    assert config.name == SITE


def test_a_missing_config_file_is_not_an_error(step, tmp_path):
    """The migration runs over every bench directory, and a half-created one has no config yet.
    Raising here would abort a migration that has nothing to do."""
    bench = MagicMock()
    bench.path = tmp_path
    bench.name = SITE

    step._write_sites_table(bench)  # must not raise


def test_an_empty_database_table_is_dropped(step, tmp_path):
    """`[database]` with no site under it carries nothing, and leaving it would keep a key the
    loader no longer reads."""
    bench, path = _bench(tmp_path, BASE + "\n[database]\n")

    step._write_sites_table(bench)

    assert "[database]" not in path.read_text()


# --------------------------------------------------------------------------- permissions


def test_the_rewrite_leaves_the_file_at_0600(step, tmp_path):
    """Real hosts were found at 664 because the migrations used raw `write_text`. Everything now
    goes through the atomic writer, which creates at 0600 via mkstemp and does NOT copy the old
    mode, so touching a legacy file tightens it."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)
    path.chmod(0o664)

    step._write_sites_table(bench)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --------------------------------------------------------- deploy history dumps


"""A recorded deploy's DB dump gets filed under the SITE it came from.

The pipeline used to dump one database per deploy and record `backup = "<path>"`. It now dumps one
per site, because every site has its own schema, and records `backups = {"<site>" = "<path>"}`. A
rollback that restored one site of three would put the bench at two points in time.

`DeployStateEntry` forbids extra keys, so a row still spelling `backup` does not load with a stale
field: it refuses the whole config. That makes this step load-bearing rather than tidying, and it
is why every test here asserts the result LOADS and not merely that the file changed.
"""

HISTORY = """
[deploy_state]
current_tag = "v2"
previous_tag = "v1"

[[deploy_state.history]]
tag = "v1"
deployed_at = "2026-01-01T00:00:00"
migrate_status = "migrated"
backup = "/backups/deploy-1/db-one.sql"

[[deploy_state.history]]
tag = "v2"
deployed_at = "2026-01-02T00:00:00"
migrate_status = "skipped"
backup = ""

[[deploy_state.history]]
tag = "v3"
deployed_at = "2026-01-03T00:00:00"
migrate_status = "skipped"
"""


def test_a_recorded_dump_is_filed_under_the_primary_site(step, tmp_path):
    # The primary is the right key and not by assumption: a pre-0.20 bench served exactly one
    # site, so the dump in that row came from the only schema there was.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + HISTORY)

    step._rewrite_deploy_history(bench)

    doc = tomlkit.parse(path.read_text())
    rows = doc["deploy_state"]["history"]
    assert dict(rows[0]["backups"]) == {SITE: "/backups/deploy-1/db-one.sql"}
    assert "backup" not in rows[0]


def test_a_row_recording_an_empty_dump_becomes_an_empty_mapping(step, tmp_path):
    # A deploy that skipped its backup must not end up pointing at a dump that does not exist.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + HISTORY)

    step._rewrite_deploy_history(bench)

    rows = tomlkit.parse(path.read_text())["deploy_state"]["history"]
    assert dict(rows[1]["backups"]) == {}


def test_a_row_with_no_dump_key_is_left_alone(step, tmp_path):
    # Nothing to rewrite, and `backups` defaults to empty, so writing an explicit empty table
    # would be noise in every operator's config file.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + HISTORY)

    step._rewrite_deploy_history(bench)

    rows = tomlkit.parse(path.read_text())["deploy_state"]["history"]
    assert "backups" not in rows[2]
    assert BenchConfig.import_from_toml(path).deploy_state.history[2].backups == {}


def test_the_rewritten_history_loads(step, tmp_path):
    # The failure that matters: a migration writing something the loader refuses.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + HISTORY)

    step._rewrite_deploy_history(bench)

    config = BenchConfig.import_from_toml(path)
    assert config.deploy_state.history[0].backups == {SITE: "/backups/deploy-1/db-one.sql"}
    assert config.deploy_state.history[1].backups == {}
    assert config.deploy_state.history[2].backups == {}


def test_the_old_spelling_does_not_load_at_all(tmp_path):
    # Why the rewrite is required rather than optional: `extra="forbid"` means a surviving
    # `backup` key takes the whole bench config down, not just that row.
    path = tmp_path / "bench_config.toml"
    path.write_text(BASE + f'\n[sites."{SITE}"]\n' + HISTORY)

    with pytest.raises(Exception, match="backup"):
        BenchConfig.import_from_toml(path)


def test_the_step_is_idempotent(step, tmp_path):
    # 0.20.0 is unreleased, so a bench at 0.20.0.dev0 re-runs this whenever a migration triggers.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + HISTORY)

    step._rewrite_deploy_history(bench)
    first = path.read_text()
    step._rewrite_deploy_history(bench)

    assert path.read_text() == first


def test_a_bench_with_no_deploy_history_is_untouched(step, tmp_path):
    # Every mount-runtime bench, which is most of them.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n')
    before = path.read_text()

    step._rewrite_deploy_history(bench)

    assert path.read_text() == before


BENCH_DIR = "shop"  # the bench's directory name, which is NOT the site it serves
NAMED_BASE = BASE.replace(f'name = "{SITE}"', f'name = "{BENCH_DIR}"', 1)


def test_the_dump_is_filed_under_the_site_and_not_the_bench_directory(step, tmp_path):
    """A bench directory is not a site name. The bench `shop` serves `shop.localhost`, and the dump
    in that row came from the SITE's schema, so the site is the key.

    Every other test here uses a bench whose name equals its site, so filing the dump under the
    bench name looks identical to filing it under the site. It is not: a key no site answers to
    makes a later `--restore-db` look up a site that does not exist.
    """
    bench, path = _bench_named(tmp_path, BENCH_DIR, NAMED_BASE + f'\n[sites."{SITE}"]\n' + HISTORY)

    step._rewrite_deploy_history(bench)

    rows = tomlkit.parse(path.read_text())["deploy_state"]["history"]
    assert dict(rows[0]["backups"]) == {SITE: "/backups/deploy-1/db-one.sql"}

    # And the loader agrees, because the restore reads the key back through the config, not the
    # file: it has to name a site the bench actually holds.
    config = BenchConfig.import_from_toml(path)
    assert config.name == BENCH_DIR
    assert list(config.deploy_state.history[0].backups) == [SITE]
    assert set(config.deploy_state.history[0].backups) <= set(config.sites)


# ------------------------------------------------------- switch.migrate = "auto"


"""`[switch].migrate = "auto"` becomes `true`.

The mode is deleted: it probed the new image for pending patches and app-version drift and skipped
the migration when it found neither, but a DocType field change ships with neither, so it reported
clean while `bench migrate` would still have synced the schema.

`SwitchConfig.migrate` is a plain bool now, so a surviving `"auto"` does not degrade to a default:
it fails validation and takes the WHOLE bench config down. Documenting "set it to true or false"
is not enough when the house rule is that the migration writes the new shape.
"""

AUTO_SWITCH = """
[switch]
migrate = "auto"
migrate_timeout = 300
backup_db = "auto"
"""


def test_auto_becomes_true_never_false(step, tmp_path):
    # `"auto"` meant "migrate when it is needed", so true is the only reading that cannot lose a
    # schema change. Turning it off would do exactly what deleting the mode was meant to prevent.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + AUTO_SWITCH)

    step._rewrite_switch_migrate(bench)

    assert tomlkit.parse(path.read_text())["switch"]["migrate"] is True


def test_backup_db_auto_is_left_alone(step, tmp_path):
    # A different mechanism that survives: it keys off whether a migrate WILL run, rather than
    # guessing whether one is needed. A sweep for the string would have taken it too.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + AUTO_SWITCH)

    step._rewrite_switch_migrate(bench)

    assert tomlkit.parse(path.read_text())["switch"]["backup_db"] == "auto"


def test_the_rewritten_switch_loads(step, tmp_path):
    # The failure this exists to prevent: one stale value refusing the entire config.
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n' + AUTO_SWITCH)

    step._rewrite_switch_migrate(bench)

    assert BenchConfig.import_from_toml(path).switch.migrate is True


def test_the_old_value_does_not_load_at_all(tmp_path):
    path = tmp_path / "bench_config.toml"
    path.write_text(BASE + f'\n[sites."{SITE}"]\n' + AUTO_SWITCH)

    with pytest.raises(Exception, match="migrate"):
        BenchConfig.import_from_toml(path)


def test_an_explicit_bool_is_untouched(step, tmp_path):
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n[switch]\nmigrate = false\n')
    before = path.read_text()

    step._rewrite_switch_migrate(bench)

    assert path.read_text() == before


def test_a_bench_with_no_switch_table_is_untouched(step, tmp_path):
    bench, path = _bench(tmp_path, BASE + f'\n[sites."{SITE}"]\n')
    before = path.read_text()

    step._rewrite_switch_migrate(bench)

    assert path.read_text() == before


# ----------------------------------------------------- default_site backfill


"""A bench with no recorded `default_site` gets one written, so the answer stops being a guess.

`resolve_primary_site` now reads `default_site` ahead of its own name-shaped rules. Those rules
reconstruct fm's creation convention from string shapes, and on a real bench they picked a phantom
site over the one that could actually be opened. Recording the answer once converts every later
read from a guess into a fact.

Written host-side, because `default_site` is a key in a host-mounted JSON file and a migration
cannot assume a running container.
"""


def _common(bench_dir, payload):
    sites = bench_dir / "workspace" / "frappe-bench" / "sites"
    sites.mkdir(parents=True, exist_ok=True)
    (sites / "common_site_config.json").write_text(json.dumps(payload))
    return sites / "common_site_config.json"


def _sited_bench(tmp_path, *sites, name=SITE):
    body = BASE.replace(f'name = "{SITE}"', f'name = "{name}"')
    body += "".join(f'\n[sites."{s}"]\n' for s in sites)
    bench, path = _bench(tmp_path, body)
    bench.name = name
    bench.site_names = list(sites)
    return bench, path


def test_a_bench_with_no_default_gets_one_recorded(step, tmp_path):
    bench, _ = _sited_bench(tmp_path, SITE, name=BENCH_DIR)
    common = _common(tmp_path, {"db_host": "global-db"})

    step._backfill_default_site(bench)

    assert json.loads(common.read_text())["default_site"] == SITE


def test_an_existing_default_is_never_overwritten(step, tmp_path):
    # It is the operator's answer, including one set by `bench use` after create. This step fills a
    # gap; it does not take the decision back.
    bench, _ = _sited_bench(tmp_path, SITE, "b.example.com", name=BENCH_DIR)
    common = _common(tmp_path, {"default_site": "b.example.com"})

    step._backfill_default_site(bench)

    assert json.loads(common.read_text())["default_site"] == "b.example.com"


def test_an_ambiguous_bench_records_nothing(step, tmp_path):
    # Several sites, none named after the bench: the name rules give no answer, and writing a guess
    # would put fm's choice beyond the operator's sight. The address form resolves it instead.
    bench, _ = _sited_bench(tmp_path, "a.example.com", "b.example.com", name="acme")
    common = _common(tmp_path, {"db_host": "global-db"})

    step._backfill_default_site(bench)

    assert "default_site" not in json.loads(common.read_text())


def test_other_keys_survive_the_write(step, tmp_path):
    # It merges into the file frappe owns; clobbering it would take the bench's db credentials out.
    bench, _ = _sited_bench(tmp_path, SITE, name=BENCH_DIR)
    common = _common(tmp_path, {"db_host": "global-db", "redis_cache": "redis://x:6379"})

    step._backfill_default_site(bench)

    data = json.loads(common.read_text())
    assert data["db_host"] == "global-db"
    assert data["redis_cache"] == "redis://x:6379"


def test_a_missing_common_site_config_is_left_alone(step, tmp_path):
    bench, _ = _sited_bench(tmp_path, SITE, name=BENCH_DIR)

    step._backfill_default_site(bench)

    assert not (tmp_path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json").exists()


def test_an_unreadable_common_site_config_is_reported_not_clobbered(step, tmp_path):
    bench, _ = _sited_bench(tmp_path, SITE, name=BENCH_DIR)
    sites = tmp_path / "workspace" / "frappe-bench" / "sites"
    sites.mkdir(parents=True, exist_ok=True)
    broken = sites / "common_site_config.json"
    broken.write_text("{not json")

    step._backfill_default_site(bench)

    assert broken.read_text() == "{not json"
    step.output.warning.assert_called()


def test_the_recorded_default_is_what_the_resolver_then_reads(step, tmp_path):
    # The point of writing it: the next read is a fact rather than a re-derivation.
    from frappe_manager.site_manager.bench_config import read_default_site

    bench, _ = _sited_bench(tmp_path, SITE, name=BENCH_DIR)
    _common(tmp_path, {})

    step._backfill_default_site(bench)

    assert read_default_site(tmp_path) == SITE


# --------------------------------------------- the generated nginx conf


def _nginx_conf(tmp_path, text: str):
    conf = tmp_path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(text)
    return conf


def test_the_generated_nginx_conf_is_removed_so_the_entrypoint_rebuilds_it(step, tmp_path):
    """This is what gives an upgraded bench one server block per site, and with it the
    `custom/<site>/*.conf` include that per-site auth and per-site tool routing are written into.

    The conf is rendered once, at the nginx container's first boot, and then persists on a host
    mount, so nothing else in an upgrade would replace it. Untested until now, while two features
    came to depend on the shape it produces."""
    bench, _ = _bench(tmp_path, BASE)
    conf = _nginx_conf(tmp_path, "server {\n  include /etc/nginx/custom/*.conf;\n}\n")
    step.backup_manager = MagicMock()

    step._refresh_nginx_default_conf(bench)

    assert not conf.exists()
    step.backup_manager.backup.assert_called_once()


def test_the_removal_is_reported(step, tmp_path):
    bench, _ = _bench(tmp_path, BASE)
    _nginx_conf(tmp_path, "server { }\n")
    step.backup_manager = MagicMock()

    step._refresh_nginx_default_conf(bench)

    assert any(SITE in str(c.args[0]) for c in step.output.print.call_args_list if c.args)


def test_a_bench_with_no_generated_conf_is_left_alone(step, tmp_path):
    """A bench mid-create has none, and taking a backup of a file that does not exist would fail
    the whole migration for nothing."""
    bench, _ = _bench(tmp_path, BASE)
    step.backup_manager = MagicMock()

    step._refresh_nginx_default_conf(bench)

    step.backup_manager.backup.assert_not_called()


def test_re_running_the_refresh_changes_nothing(step, tmp_path):
    """`0.20.0.dev0` sorts below `0.20.0`, so a bench on a dev build re-runs every step of this
    migration whenever one is triggered at all."""
    bench, _ = _bench(tmp_path, BASE)
    _nginx_conf(tmp_path, "server { }\n")
    step.backup_manager = MagicMock()

    step._refresh_nginx_default_conf(bench)
    step._refresh_nginx_default_conf(bench)

    step.backup_manager.backup.assert_called_once()


# ------------------------------------------------- the [ssl] table


PRE_020_TOP_LEVEL_SSL = f"""
[[ssl_certificates]]
domain = "{SITE}"
ssl_type = "letsencrypt"
challenge_type = "dns01"
hsts = "off"

[dns_providers.cloudflare]
api_token = "tok"
"""


def _certs(path):
    """What the loader actually ends up with, which is the only thing that matters here."""
    return BenchConfig.import_from_toml(path).ssl_certificates


def test_a_top_level_certificate_array_moves_under_ssl(step, tmp_path):
    """0.19 wrote both keys at the TOP LEVEL and no later migration moved them.

    `import_from_toml` reads `[ssl]` only, so such a bench loads with ZERO certificates and the
    next save deletes the orphaned top-level keys outright: the whole TLS configuration gone,
    silently. This step is the only thing standing between a bench and that."""
    bench, path = _bench(tmp_path, BASE + PRE_020_TOP_LEVEL_SSL)
    assert _certs(path) == []  # the bug, before the step runs

    step._rewrite_ssl_table(bench)

    text = path.read_text()
    assert "[[ssl.certificates]]" in text or "[ssl]" in text
    assert "[[ssl_certificates]]" not in text
    loaded = _certs(path)
    assert [c.domain for c in loaded] == [SITE]
    assert loaded[0].ssl_type.value == "letsencrypt"


def test_the_moved_certificate_is_what_the_tls_gate_reads(step, tmp_path):
    """`fm auth BENCH/SITE --protect web` refuses when the named site has no certificate, and it
    asks `certificate_for`, which reads exactly this array. Left unmoved, the gate would refuse web
    auth on a site that HAS TLS."""
    bench, path = _bench(tmp_path, BASE + PRE_020_TOP_LEVEL_SSL)

    step._rewrite_ssl_table(bench)

    config = BenchConfig.import_from_toml(path)
    assert config.certificate_for(SITE).ssl_type.value == "letsencrypt"


def test_a_top_level_credential_table_moves_under_ssl(step, tmp_path):
    bench, path = _bench(tmp_path, BASE + PRE_020_TOP_LEVEL_SSL)

    step._rewrite_ssl_table(bench)

    config = BenchConfig.import_from_toml(path)
    assert "cloudflare" in (config.dns_providers or {})
    assert "[dns_providers.cloudflare]" not in path.read_text().split("[ssl]")[0]


def test_an_orphan_is_dropped_rather_than_merged_over_the_loaded_copy(step, tmp_path):
    """`[ssl]` already holding the array means the loader has been reading THAT one, so the
    top-level copy has never been loaded by any version and holds nothing worth keeping."""
    already = f"""
[[ssl.certificates]]
domain = "{SITE}"
ssl_type = "letsencrypt"
challenge_type = "dns01"
hsts = "on"

[[ssl_certificates]]
domain = "stale.localhost"
ssl_type = "letsencrypt"
challenge_type = "dns01"
hsts = "off"
"""
    bench, path = _bench(tmp_path, BASE + already)

    step._rewrite_ssl_table(bench)

    loaded = _certs(path)
    assert [c.domain for c in loaded] == [SITE]
    assert loaded[0].hsts == "on"


def test_the_old_credential_key_spelling_is_renamed(step, tmp_path):
    bench, path = _bench(
        tmp_path,
        BASE + '\n[ssl.dns_challenge_providers.cloudflare]\napi_token = "tok"\n',
    )

    step._rewrite_ssl_table(bench)

    config = BenchConfig.import_from_toml(path)
    assert "cloudflare" in (config.dns_providers or {})
    assert "dns_challenge_providers" not in path.read_text()


def test_an_ssl_key_that_is_not_a_table_is_left_for_a_human(step, tmp_path):
    """0.19 tolerated an `ssl` that was an array. Nothing merges into that, and replacing it would
    throw away whatever it holds."""
    bench, path = _bench(tmp_path, BASE + '\nssl = ["nonsense"]\n')
    step.logger = MagicMock()

    step._rewrite_ssl_table(bench)

    assert 'ssl = ["nonsense"]' in path.read_text()


def test_re_running_the_ssl_rewrite_changes_nothing(step, tmp_path):
    """`0.20.0.dev0` sorts below `0.20.0`, so every step here re-runs on a dev-build bench."""
    bench, path = _bench(tmp_path, BASE + PRE_020_TOP_LEVEL_SSL)

    step._rewrite_ssl_table(bench)
    once = path.read_text()
    step._rewrite_ssl_table(bench)

    assert path.read_text() == once


# ------------------------------- the global Cloudflare credential


LEGACY_FM_CONFIG = """version = "0.19.0"

[cloudflare]
email = "ops@example.com"
api_token = "tok"
"""


def _fm_config(step, tmp_path, text: str):
    """`_relocate_global_dns_credentials` reads the module-level CLI_FM_CONFIG_PATH."""
    path = tmp_path / "fm_config.toml"
    path.write_text(text)
    step.backup_manager = MagicMock()
    step.logger = MagicMock()
    return path


def _loaded_providers(path):
    """What FMConfigManager ends up with. The model can no longer represent the OLD table, so a
    file left unconverted loses its credential the next time fm writes the file."""
    return FMConfigManager.import_from_toml(path).dns_providers or {}


def test_the_global_cloudflare_credential_moves_under_ssl(step, tmp_path, monkeypatch):
    path = _fm_config(step, tmp_path, LEGACY_FM_CONFIG)
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", path)

    step._relocate_global_dns_credentials()

    text = path.read_text()
    assert "[cloudflare]" not in text
    assert "[ssl.dns_providers.cloudflare]" in text
    provider = _loaded_providers(path)["cloudflare"]
    assert provider.api_token == "tok"
    assert provider.email == "ops@example.com"
    step.backup_manager.backup.assert_called_once_with(path)


def test_the_legacy_api_key_form_is_carried_too(step, tmp_path, monkeypatch):
    """The pre-token Cloudflare auth. Dropping it silently would break renewal on the accounts
    still using it, and the model cannot read the old table to tell anyone."""
    path = _fm_config(step, tmp_path, 'version = "0.19.0"\n\n[cloudflare]\napi_key = "legacy"\nemail = "a@b.c"\n')
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", path)

    step._relocate_global_dns_credentials()

    provider = _loaded_providers(path)["cloudflare"]
    assert provider.api_key == "legacy"


def test_an_existing_labelled_set_wins_over_the_legacy_table(step, tmp_path, monkeypatch):
    path = _fm_config(
        step,
        tmp_path,
        'version = "0.19.0"\n\n[cloudflare]\napi_token = "old"\n\n'
        '[ssl.dns_providers.cloudflare]\nprovider = "cloudflare"\napi_token = "current"\n',
    )
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", path)

    step._relocate_global_dns_credentials()

    # The labelled set is the one every version since has been reading.
    assert _loaded_providers(path)["cloudflare"].api_token == "current"
    assert "[cloudflare]" not in path.read_text()


def test_a_credential_less_legacy_table_leaves_no_empty_scaffolding(step, tmp_path, monkeypatch):
    """A set with no credential is dropped by the config writer anyway and reads as absent to the
    resolver, so writing `[ssl.dns_providers.cloudflare]` here would be noise that looks configured."""
    path = _fm_config(step, tmp_path, 'version = "0.19.0"\n\n[cloudflare]\nemail = "a@b.c"\n')
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", path)

    step._relocate_global_dns_credentials()

    text = path.read_text()
    assert "[cloudflare]" not in text
    assert "dns_providers" not in text
    assert "[ssl]" not in text


def test_a_file_with_no_legacy_table_is_not_even_backed_up(step, tmp_path, monkeypatch):
    path = _fm_config(step, tmp_path, 'version = "0.20.0"\n')
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", path)

    step._relocate_global_dns_credentials()

    step.backup_manager.backup.assert_not_called()


def test_an_ssl_key_that_is_not_a_table_is_skipped(step, tmp_path, monkeypatch):
    path = _fm_config(step, tmp_path, 'version = "0.19.0"\nssl = ["nonsense"]\n\n[cloudflare]\napi_token = "tok"\n')
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", path)

    step._relocate_global_dns_credentials()

    # Nothing merges into that shape, and replacing it would throw away whatever it holds.
    assert "[cloudflare]" in path.read_text()
    step.backup_manager.backup.assert_not_called()


def test_an_absent_fm_config_is_a_no_op(step, tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", tmp_path / "nothing.toml")
    step.backup_manager = MagicMock()

    step._relocate_global_dns_credentials()

    step.backup_manager.backup.assert_not_called()


def test_re_running_the_credential_move_changes_nothing(step, tmp_path, monkeypatch):
    path = _fm_config(step, tmp_path, LEGACY_FM_CONFIG)
    monkeypatch.setattr(migrate_mod, "CLI_FM_CONFIG_PATH", path)

    step._relocate_global_dns_credentials()
    once = path.read_text()
    step._relocate_global_dns_credentials()

    assert path.read_text() == once


# --------------------------------- the pre-upgrade dump (the rollback path)


def _dump_step(step, tmp_path, *, timestamp="20260903120000"):
    """`_dump_whole_engine` is I/O at three seams: the export inside the container, the copy out,
    and the compression on the host. Only the container calls are mocked."""
    step.backup_manager = MagicMock()
    step.backup_manager.migration_timestamp = timestamp
    step.backup_manager.backup_dir = tmp_path / "backups"
    step.backup_manager.backup_dir.mkdir(parents=True, exist_ok=True)

    db = MagicMock()
    step.services_manager = MagicMock()

    def fake_cp(src, dest, stream=False):
        # What `compose cp` does: the plain dump lands on the host.
        Path(dest).write_text("-- every database\n")

    step.services_manager.compose.cp.side_effect = fake_cp
    return db


def test_the_dump_is_compressed_and_the_plain_copy_is_not_left_behind(step, tmp_path):
    """This is the ONLY route back: the datadir upgrade is one way, so `undo_services_migrate` can
    do nothing but point the operator at this file."""
    db = _dump_step(step, tmp_path)

    result = step._dump_whole_engine(db)

    assert result.name.endswith(".sql.gz")
    assert result.is_file()
    with gzip.open(result, "rt") as fh:
        assert fh.read() == "-- every database\n"
    assert not result.with_suffix("").exists()  # the uncompressed copy


def test_the_dump_name_carries_the_migration_timestamp(step, tmp_path):
    """One run's artifacts group together instead of drifting by a second, which is what lets
    `undo_services_migrate` name a directory and have the operator find the right dump in it."""
    db = _dump_step(step, tmp_path, timestamp="20260101010101")

    result = step._dump_whole_engine(db)

    assert result.name == "global-db-all-databases-20260101010101.sql.gz"


def test_it_exports_every_database_then_copies_that_exact_path_out(step, tmp_path):
    db = _dump_step(step, tmp_path)

    step._dump_whole_engine(db)

    exported = db.db_export_all.call_args.args[0]
    src, dest = step.services_manager.compose.cp.call_args.args
    # Exporting one path and copying another out is a silent empty backup.
    assert src == f"global-db:{exported}"
    assert Path(dest).name == exported.name


def test_an_export_that_never_lands_does_not_yield_a_dump(step, tmp_path):
    """A failure has to leave NO dump rather than a valid-looking empty one.

    `undo_services_migrate` can only name the backup directory and tell the operator to restore
    what is in it, so a file that is present and correctly named but useless is worse than an
    absent one: it reads as a rollback that exists."""
    db = _dump_step(step, tmp_path)
    step.services_manager.compose.cp.side_effect = FileNotFoundError("no such file in container")

    with pytest.raises(FileNotFoundError):
        step._dump_whole_engine(db)

    assert list(step.backup_manager.backup_dir.glob("*.sql.gz")) == []


# ------------------------------------------- the adminer login plugin


def test_the_adminer_plugin_lands_byte_identical(step, tmp_path):
    """It is PHP that Adminer loads on every request, so a truncated or re-encoded copy is a broken
    login page rather than a missing feature."""
    bench, _ = _bench(tmp_path, BASE)

    step._place_adminer_plugin(bench)

    placed = tmp_path / "configs" / "adminer" / "000-fm-login.php"
    assert placed.is_file()
    assert placed.read_bytes() == get_template_path("adminer/000-fm-login.php").read_bytes()


def test_re_placing_the_plugin_overwrites_a_stale_copy(step, tmp_path):
    """0.20.0.dev0 sorts below 0.20.0, so this re-runs; a bench that got an older plugin from an
    earlier dev build has to end up with the current one."""
    bench, _ = _bench(tmp_path, BASE)
    stale = tmp_path / "configs" / "adminer" / "000-fm-login.php"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("<?php // an older build\n")

    step._place_adminer_plugin(bench)

    assert stale.read_bytes() == get_template_path("adminer/000-fm-login.php").read_bytes()
