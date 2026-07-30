"""The compose surgery that moves global-db onto a newer engine.

Everything else in that migration is Docker I/O (dump, stop, recreate, wait), which
a unit test can only assert back at itself. The rewrite is the part with real
decisions in it, so it lives as a pure function and is pinned here.
"""

import copy

from ruamel.yaml import YAML

from frappe_manager import GLOBAL_DB_IMAGE
from frappe_manager.migration_manager.migrations.migrate_0_20_0 import (
    STALE_ENGINE_FLAG,
    rewrite_global_db_service,
)

# The global-db block as it shipped before this migration: the EOL engine, four
# command flags, and no auto-upgrade switch.
_PRE_MIGRATION_SERVICE = """
image: mariadb:10.6
user: 1000:1000
restart: always
command:
- --character-set-server=utf8mb4
- --collation-server=utf8mb4_unicode_ci
- --skip-character-set-client-handshake
- --skip-innodb-read-only-compressed
environment:
  MYSQL_ROOT_PASSWORD_FILE: /run/secrets/db_root_password
  MYSQL_DATABASE: fm_db
volumes:
- ./mariadb/data:/var/lib/mysql
- ./mariadb/conf:/etc/mysql
"""


def _service() -> dict:
    return YAML().load(_PRE_MIGRATION_SERVICE)


def test_engine_is_repointed_at_the_pinned_image():
    engine = _service()

    rewrite_global_db_service(engine)

    assert engine["image"] == GLOBAL_DB_IMAGE


def test_the_stale_compressed_tables_flag_is_dropped():
    # It was only needed on MariaDB 10.6.1-10.6.5. Leaving it behind is how a setup
    # keeps carrying workarounds for versions it no longer runs.
    engine = _service()
    assert STALE_ENGINE_FLAG in engine["command"]

    rewrite_global_db_service(engine)

    assert STALE_ENGINE_FLAG not in engine["command"]
    assert engine["command"] == [
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_unicode_ci",
        "--skip-character-set-client-handshake",
    ]


def test_auto_upgrade_is_added_so_system_tables_are_not_left_behind():
    # Without it the engine boots on a new version against the previous version's
    # system tables, which surfaces later as confusing privilege errors.
    engine = _service()
    assert "MARIADB_AUTO_UPGRADE" not in engine["environment"]

    rewrite_global_db_service(engine)

    assert engine["environment"]["MARIADB_AUTO_UPGRADE"] == 1


def test_an_operators_explicit_auto_upgrade_choice_is_not_overwritten():
    engine = _service()
    engine["environment"]["MARIADB_AUTO_UPGRADE"] = 0

    rewrite_global_db_service(engine)

    assert engine["environment"]["MARIADB_AUTO_UPGRADE"] == 0


def test_a_service_without_an_environment_block_gets_one():
    # Otherwise there is nowhere to put the switch and it would be silently skipped.
    engine = _service()
    del engine["environment"]

    rewrite_global_db_service(engine)

    assert engine["environment"]["MARIADB_AUTO_UPGRADE"] == 1


def test_a_service_without_a_command_block_is_left_alone():
    engine = _service()
    del engine["command"]

    rewrite_global_db_service(engine)

    assert "command" not in engine
    assert engine["image"] == GLOBAL_DB_IMAGE


def test_nothing_else_in_the_service_is_disturbed():
    # The migration edits a file the operator also owns; volumes, user and restart
    # policy must come through untouched.
    engine = _service()
    before = copy.deepcopy(engine)

    rewrite_global_db_service(engine)

    for key in ("user", "restart", "volumes"):
        assert engine[key] == before[key], key
    assert engine["environment"]["MYSQL_ROOT_PASSWORD_FILE"] == before["environment"]["MYSQL_ROOT_PASSWORD_FILE"]
    assert engine["environment"]["MYSQL_DATABASE"] == before["environment"]["MYSQL_DATABASE"]


def test_applying_it_twice_changes_nothing_further():
    # The migration early-returns when already on the pinned image, but the rewrite
    # itself has to be safe to repeat: a half-finished run must be resumable.
    engine = _service()

    rewrite_global_db_service(engine)
    once = copy.deepcopy(engine)
    rewrite_global_db_service(engine)

    assert engine == once


def test_the_pinned_image_is_what_the_rewrite_defaults_to():
    # A caller passing nothing must land on the same tag the templates carry, or new
    # installs and migrated ones would diverge.
    engine = _service()

    rewrite_global_db_service(engine)

    assert engine["image"] == GLOBAL_DB_IMAGE
    assert engine["image"] != "mariadb:10.6"
