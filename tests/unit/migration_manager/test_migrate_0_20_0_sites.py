"""`[database."<site>"]` becomes `[sites."<site>".database]`, in the 0.20.0 migration.

A bench holds exactly one site today and its name is the bench's, so that table already had a site
as its key. The move gives the site somewhere to hold its other per-site facts later, instead of
them accumulating as top-level keys that are only per-site because there happens to be one site.

`import_from_toml` reads only the new spelling. That is the established pattern in this release, not
a new risk: the same migration renames `dns_challenge_providers` to `dns_providers` and reads only
that. So the tests below assert the file shape AND that the result loads, because a migration that
writes something the loader cannot read is the failure that matters.

This step must be idempotent, and not as a nicety: 0.20.0 is unreleased, so a bench recorded at
`0.20.0.dev0` sorts BELOW `0.20.0` and re-runs this migration whenever one is triggered at all.
"""

import stat
from unittest.mock import MagicMock

import pytest

from frappe_manager.migration_manager.migrations.migrate_0_20_0 import MigrationV0200
from frappe_manager.site_manager.bench_config import BenchConfig

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


# --------------------------------------------------------------------------- the move


def test_the_database_table_moves_under_the_site(step, tmp_path):
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._move_database_under_sites(bench)

    text = path.read_text()
    assert f'[sites."{SITE}".database]' in text
    assert f'[database."{SITE}"]' not in text


def test_every_field_survives_the_move(step, tmp_path):
    """A dropped `ca` means a site that silently stops verifying TLS, and a dropped `port` means it
    cannot connect at all, so this asserts the whole record rather than a sample of it."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._move_database_under_sites(bench)
    database = BenchConfig.import_from_toml(path).get_database_config()

    assert (database.host, database.port, database.name) == ("rds.internal", 3307, "app_prod")
    assert (database.user, database.ca) == ("app_svc", "/host/rds.pem")
    assert database.check_hostname is False


def test_the_migrated_file_is_what_the_loader_reads(step, tmp_path):
    """The lookup is keyed by site, so the move has to land under the right key and not merely
    somewhere under `[sites]`."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._move_database_under_sites(bench)
    config = BenchConfig.import_from_toml(path)

    assert config.get_database_config(SITE) is not None
    assert config.get_database_config("other.localhost") is None


def test_unrelated_config_is_left_alone(step, tmp_path):
    """The step rewrites the whole document, so everything it is not about has to come back."""
    extra = '\n[redis]\ncache = "redis://r.example:6379/0"\nqueue = "redis://r.example:6379/1"\n'
    bench, path = _bench(tmp_path, BASE + 'alias_domains = ["www.shop.example.com"]\n' + EXTERNAL + extra)

    step._move_database_under_sites(bench)
    config = BenchConfig.import_from_toml(path)

    assert config.alias_domains == ["www.shop.example.com"]
    assert config.redis is not None
    assert config.redis.cache == "redis://r.example:6379/0"


# --------------------------------------------------------------------------- re-running


def test_running_it_again_changes_nothing(step, tmp_path):
    """Required, not merely tidy: a bench at `0.20.0.dev0` re-runs this migration, because a dev
    release sorts below its own final version."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)

    step._move_database_under_sites(bench)
    once = path.read_text()
    step._move_database_under_sites(bench)
    step._move_database_under_sites(bench)

    assert path.read_text() == once


def test_an_already_migrated_site_is_not_overwritten_by_a_stale_table(step, tmp_path):
    """If both shapes are present, the one under the site is the migrated copy and wins. Preferring
    the top-level table would undo a previous run."""
    both = BASE + EXTERNAL + f'\n[sites."{SITE}".database]\nhost = "migrated.internal"\nname = "app_prod"\n'
    bench, path = _bench(tmp_path, both)

    step._move_database_under_sites(bench)

    assert BenchConfig.import_from_toml(path).get_database_config().host == "migrated.internal"


# --------------------------------------------------------------------------- nothing to do


def test_a_bench_with_no_external_database_is_untouched(step, tmp_path):
    """Most benches are on `global-db` and have no `[database]` table at all."""
    bench, path = _bench(tmp_path, BASE)
    before = path.read_text()

    step._move_database_under_sites(bench)

    assert path.read_text() == before


def test_a_missing_config_file_is_not_an_error(step, tmp_path):
    """The migration runs over every bench directory, and a half-created one has no config yet.
    Raising here would abort a migration that has nothing to do."""
    bench = MagicMock()
    bench.path = tmp_path
    bench.name = SITE

    step._move_database_under_sites(bench)  # must not raise


def test_an_empty_database_table_is_dropped(step, tmp_path):
    """`[database]` with no site under it carries nothing, and leaving it would keep a key the
    loader no longer reads."""
    bench, path = _bench(tmp_path, BASE + "\n[database]\n")

    step._move_database_under_sites(bench)

    assert "[database]" not in path.read_text()


# --------------------------------------------------------------------------- permissions


def test_the_rewrite_leaves_the_file_at_0600(step, tmp_path):
    """Real hosts were found at 664 because the migrations used raw `write_text`. Everything now
    goes through the atomic writer, which creates at 0600 via mkstemp and does NOT copy the old
    mode, so touching a legacy file tightens it."""
    bench, path = _bench(tmp_path, BASE + EXTERNAL)
    path.chmod(0o664)

    step._move_database_under_sites(bench)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
