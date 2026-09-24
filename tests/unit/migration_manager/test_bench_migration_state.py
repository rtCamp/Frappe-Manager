"""The bench version probe must be schema-tolerant.

It runs before every command; a bench_config.toml that FAILS pydantic
validation must still report its real schema version -- otherwise config
errors are masked as a bogus "migration required (v0.0.0)" prompt and the user
is sent to `fm migrate`, which cannot fix a config error.
"""

from frappe_manager.migration_manager.bench_migration_state import (
    bench_needs_migration,
    get_bench_migration_version,
)
from frappe_manager.migration_manager.version import Version

VALID_STATE = """\
name = "x.localhost"
[schema]
version = "1.0.0"
last_migration_date = "2026-07-24T00:00:00"
"""

LEGACY_STATE = """\
name = "x.localhost"
[migration_state]
migrated_to = "1.0.0"
last_migration_date = "2026-07-24T00:00:00"
"""

SCHEMA_INVALID = """\
name = "x.localhost"
[switch]
backup_db = false
rollback_db = true
[schema]
version = "1.0.0"
last_migration_date = "2026-07-24T00:00:00"
"""


def _bench(tmp_path, content):
    (tmp_path / "bench_config.toml").write_text(content)
    return tmp_path


def test_version_read_from_valid_config(tmp_path):
    b = _bench(tmp_path, VALID_STATE)
    assert get_bench_migration_version(b) == Version("1.0.0")


def test_version_read_from_the_pre_rename_migration_state_table(tmp_path):
    """`[migration_state].migrated_to` is the table/key pair 1.0.0's migration renames on disk,
    still read here so a not-yet-migrated bench reports its real version instead of 0.0.0."""
    b = _bench(tmp_path, LEGACY_STATE)
    assert get_bench_migration_version(b) == Version("1.0.0")


def test_version_read_from_a_legacy_table_holding_the_new_key(tmp_path):
    """The table fallback and the key fallback are independent: a `[migration_state]` table
    that already carries `version` (not `migrated_to`) must still resolve, not just the pairing
    a straight pre/post-rename file would actually have on disk."""
    b = _bench(tmp_path, 'name = "x.localhost"\n[migration_state]\nversion = "1.0.0"\n')
    assert get_bench_migration_version(b) == Version("1.0.0")


def test_version_read_from_the_new_table_holding_the_legacy_key(tmp_path):
    """Same independence the other way: a `[schema]` table that still carries `migrated_to`
    (e.g. hand-renamed only the table) must still resolve."""
    b = _bench(tmp_path, 'name = "x.localhost"\n[schema]\nmigrated_to = "1.0.0"\n')
    assert get_bench_migration_version(b) == Version("1.0.0")


def test_schema_invalid_config_keeps_real_version(tmp_path):
    # The regression: an invalid [switch] combo must NOT degrade to v0.0.0.
    b = _bench(tmp_path, SCHEMA_INVALID)
    assert get_bench_migration_version(b) == Version("1.0.0")
    assert not bench_needs_migration(b, Version("1.0.0"))


def test_missing_file_and_missing_state(tmp_path):
    assert get_bench_migration_version(tmp_path) == Version("0.0.0")
    b = _bench(tmp_path, 'name = "x.localhost"\n')
    assert get_bench_migration_version(b) == Version("0.0.0")


def test_unparseable_toml_degrades_quietly(tmp_path):
    b = _bench(tmp_path, "not [ valid toml ===")
    assert get_bench_migration_version(b) == Version("0.0.0")


# ======================================================================================
# set_bench_migration_version -- must not delete a retained stray while bumping the version.
#
# `fm migrate` is the command whose entire job is to fix an out-of-date bench_config.toml. It used
# to rebuild `[schema]` from a fresh `SchemaState(version=..., last_migration_date=
# ...)`, which drops any OTHER key already retained there (SchemaState is extra="allow") because
# a freshly constructed instance never saw that kwarg. That is exactly the outcome the retention
# ruling forbids: fm never deletes a key it does not understand, and it is worst of all here, since
# the command runs precisely because the file needed fixing.
# ======================================================================================

import tomlkit

from frappe_manager.migration_manager.bench_migration_state import set_bench_migration_version

STATE_WITH_STRAY = """\
name = "x.localhost"
developer_mode = false
admin_tools = false
environment_type = "prod"
[schema]
version = "0.19.0"
last_migration_date = "2026-07-24T00:00:00"
migrated_at = "operator_typo_value"
"""

LEGACY_STATE_WITH_STRAY = """\
name = "x.localhost"
developer_mode = false
admin_tools = false
environment_type = "prod"
[migration_state]
migrated_to = "0.19.0"
last_migration_date = "2026-07-24T00:00:00"
migrated_at = "operator_typo_value"
"""


def test_set_bench_migration_version_preserves_a_stray_key_in_schema(tmp_path):
    b = _bench(tmp_path, STATE_WITH_STRAY)

    set_bench_migration_version(b, Version("1.0.0"))

    doc = tomlkit.parse((b / "bench_config.toml").read_text())
    state = dict(doc["schema"])
    # The stray survives, value intact -- not just its name.
    assert state["migrated_at"] == "operator_typo_value"
    # And the write this call exists to make still happened.
    assert state["version"] == "1.0.0"


def test_set_bench_migration_version_preserves_a_stray_key_from_a_legacy_migration_state_table(tmp_path):
    """The stray is read out of a pre-rename `[migration_state]` table. The writer MERGES rather
    than strips (only the migration removes an old table on disk), so both tables coexist after
    this call, but `[schema]` -- the one every reader now trusts -- carries the stray and the
    bumped version."""
    b = _bench(tmp_path, LEGACY_STATE_WITH_STRAY)

    set_bench_migration_version(b, Version("1.0.0"))

    doc = tomlkit.parse((b / "bench_config.toml").read_text())
    state = dict(doc["schema"])
    assert state["migrated_at"] == "operator_typo_value"
    assert state["version"] == "1.0.0"


def test_set_bench_migration_version_with_no_prior_schema_table_still_writes_one(tmp_path):
    """No [schema] table to preserve; a fresh one is created, not an error."""
    b = _bench(tmp_path, 'name = "x.localhost"\ndeveloper_mode = false\nadmin_tools = false\nenvironment_type = "prod"\n')

    set_bench_migration_version(b, Version("1.0.1"))

    doc = tomlkit.parse((b / "bench_config.toml").read_text())
    assert dict(doc["schema"])["version"] == "1.0.1"
