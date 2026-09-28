"""`[schema].version` is the ONE version key in fm_config.toml.

Before P2 there were two (`system_migrated_to` and the top-level `version`), read by different
consumers (the gates read the first, the executor's discovery read the second) and stamped from
three places -- the state that produces "gate says migrate, executor says nothing to do". The
1.0.0 rename retired the table's own then-current key, `migrated_to`, in favour of `version`, and
the table itself, `[migration_state]`, in favour of `[schema]` -- both fallbacks apply
independently of each other, so either name can appear with either key. Legacy spellings are
understood in exactly one place, `import_from_toml`'s seed, because the ledger is read BEFORE any
migration can run: without the seed a v1.0.0 host reads 0.0.0 and discovery re-selects the frozen
v0.19/v1.0.0 migrations against it. The write path owns the disk cutover: the first stamp pops
`migrated_to`/`system_migrated_to` and the export prune retires the top-level `version` key.
"""

from pathlib import Path

import tomlkit

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.migration_manager.version import Version


def _config(tmp_path, text: str) -> Path:
    path = tmp_path / "fm_config.toml"
    path.write_text(text)
    return path


class TestLedgerSeeding:
    """Legacy files read correctly through the single-key getter, memory only."""

    def test_pre_rename_spelling_reads_as_the_ledger(self, tmp_path):
        path = _config(tmp_path, '[migration_state]\nsystem_migrated_to = "1.0.0"\n')

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("1.0.0")

    def test_a_legacy_table_holding_the_new_key_reads_as_the_ledger(self, tmp_path):
        """The table fallback and the key fallback are independent: a `[migration_state]` table
        that already carries `version` (not `migrated_to`) must still resolve, not just the
        pairing a straight pre/post-rename file would actually have on disk."""
        path = _config(tmp_path, '[migration_state]\nversion = "1.0.0"\n')

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("1.0.0")

    def test_pre_ledger_file_reads_the_retired_version_key(self, tmp_path):
        """Hosts from before the ledger existed carried only the top-level `version`."""
        path = _config(tmp_path, 'version = "0.19.0"\n')

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("0.19.0")

    def test_migrated_to_wins_over_both_legacy_spellings(self, tmp_path):
        path = _config(
            tmp_path,
            'version = "0.19.0"\n[migration_state]\nmigrated_to = "1.0.1"\nsystem_migrated_to = "1.0.0"\n',
        )

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("1.0.1")

    def test_the_new_table_still_prefers_migrated_to_over_system_migrated_to(self, tmp_path):
        """Same key precedence, spelled with the new table name: the table rename never changes
        which of the two legacy keys wins inside it."""
        path = _config(tmp_path, '[schema]\nmigrated_to = "1.0.1"\nsystem_migrated_to = "1.0.0"\n')

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("1.0.1")

    def test_the_new_key_wins_over_every_legacy_spelling_when_present(self, tmp_path):
        path = _config(
            tmp_path,
            'version = "0.18.0"\n[schema]\nversion = "2.0.0"\nmigrated_to = "1.0.1"\nsystem_migrated_to = "1.0.0"\n',
        )

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("2.0.0")

    def test_old_spelling_wins_over_the_retired_version_key(self, tmp_path):
        """`system_migrated_to` was the real ledger; `version` merely tracked the CLI."""
        path = _config(tmp_path, 'version = "1.0.1"\n[migration_state]\nsystem_migrated_to = "1.0.0"\n')

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("1.0.0")

    def test_a_file_with_no_version_information_reads_unknown(self, tmp_path):
        path = _config(tmp_path, 'ngrok_auth_token = "x"\n')

        config = FMConfigManager.import_from_toml(path)

        assert config.get_system_migration_version() == Version("0.0.0")

    def test_seeding_never_writes_the_file(self, tmp_path):
        text = 'version = "0.19.0"\n[migration_state]\nsystem_migrated_to = "1.0.0"\n'
        path = _config(tmp_path, text)

        FMConfigManager.import_from_toml(path)

        assert path.read_text() == text


class TestDiskCutover:
    """The write path leaves exactly one version key on disk, under the new table and key."""

    def test_first_stamp_on_a_legacy_file_leaves_exactly_one_version_key(self, tmp_path):
        path = _config(tmp_path, 'version = "1.0.0"\n[migration_state]\nsystem_migrated_to = "1.0.0"\n')

        config = FMConfigManager.import_from_toml(path)
        config.set_system_migration_version(Version("1.0.1"))

        doc = tomlkit.parse(path.read_text())
        assert "version" not in doc  # the top-level scalar is retired by the export prune
        assert "migration_state" not in doc  # the old table name never reappears
        assert dict(doc["schema"]) == {"version": "1.0.1"}

    def test_an_ordinary_save_retires_the_version_key_but_never_invents_a_ledger(self, tmp_path):
        """`version` is pruned like `[cloudflare]` was; the seeded ledger value is persisted
        under the current spelling, so the file converges without a stamp."""
        path = _config(tmp_path, 'version = "1.0.0"\n')

        FMConfigManager.import_from_toml(path).export_to_toml(path)

        doc = tomlkit.parse(path.read_text())
        assert "version" not in doc
        assert dict(doc["schema"]) == {"version": "1.0.0"}
        # And a reload agrees with what the original file meant.
        assert FMConfigManager.import_from_toml(path).get_system_migration_version() == Version("1.0.0")


class TestExportPathDefault:
    def test_the_setter_saves_to_the_file_the_config_was_loaded_from(self, tmp_path, monkeypatch):
        """`set_system_migration_version` exports through the DEFAULT path. With the module
        constant as that default, a config imported from any other path silently wrote the
        operator's real ~/frappe/fm_config.toml -- observed from this very suite clobbering a
        live host's ledger, network table and ngrok token. The default is the loaded path."""
        decoy = tmp_path / "decoy" / "fm_config.toml"
        decoy.parent.mkdir()
        decoy.write_text("")
        monkeypatch.setattr("frappe_manager.metadata_manager.CLI_FM_CONFIG_PATH", decoy)
        path = _config(tmp_path, '[migration_state]\nmigrated_to = "1.0.0"\n')

        config = FMConfigManager.import_from_toml(path)
        config.set_system_migration_version(Version("1.0.1"))

        assert 'version = "1.0.1"' in path.read_text()
        assert decoy.read_text() == ""


class TestANewHostIsBaselinedByTheWriter:
    """Creating the file and recording its version are ONE operation, the rule every schema
    migration tool converged on (`alembic stamp head`, `flyway baseline`, `rails db:schema:load`).

    fm learned it the hard way: the file came into existence as a byproduct of whichever setting
    was saved first -- the auto-sized subnet, an ngrok token, DNS credentials -- and the stamp
    lived somewhere else entirely, behind a different condition. `fm list` on a brand new host
    wrote an unstamped config, every later command read 0.0.0 and demanded a migration, and the
    migration refused an unknown version. No command resolved it. Enforcing it in the writer is
    what makes an unstamped config unconstructable, whichever caller happens to be first.
    """

    def _fm_version(self) -> str:
        from frappe_manager.utils.helpers import get_current_fm_version

        return get_current_fm_version()

    def test_a_file_the_writer_creates_carries_the_current_version(self, tmp_path):
        path = tmp_path / "fm_config.toml"

        FMConfigManager.import_from_toml(path).export_to_toml(path)

        assert dict(tomlkit.parse(path.read_text())["schema"]) == {"version": self._fm_version()}

    def test_a_caller_that_knows_nothing_about_migrations_still_produces_a_stamped_file(self, tmp_path):
        """The subnet writer in services.py is the one that actually created it in the field."""
        path = tmp_path / "fm_config.toml"
        config = FMConfigManager.import_from_toml(path)
        config.network.subnet_cidr = "10.2.0.0/16"

        config.export_to_toml(path)

        assert FMConfigManager.import_from_toml(path).get_system_migration_version() == Version(self._fm_version())

    def test_an_existing_file_is_never_baselined(self, tmp_path):
        """A host whose config predates this fm must keep reading as unknown, so the gate refuses
        loudly instead of declaring an old install current and skipping its migration."""
        path = _config(tmp_path, 'ngrok_auth_token = "x"\n')

        FMConfigManager.import_from_toml(path).export_to_toml(path)

        assert FMConfigManager.import_from_toml(path).get_system_migration_version() == Version("0.0.0")

    def test_a_host_that_already_has_benches_is_not_a_new_host(self, tmp_path):
        """Config deleted, benches kept. Baselining would silently skip the migration those benches
        need; fm refuses to guess and the gate says so. Flyway's `baselineOnMigrate` carries the
        same caveat."""
        (tmp_path / "sites" / "mybench").mkdir(parents=True)
        path = tmp_path / "fm_config.toml"

        FMConfigManager.import_from_toml(path).export_to_toml(path)

        assert FMConfigManager.import_from_toml(path).get_system_migration_version() == Version("0.0.0")

    def test_an_empty_benches_directory_is_still_a_new_host(self, tmp_path):
        """The callback creates `sites/` before anything writes the config, so its mere existence
        cannot be the signal -- only a bench inside it is."""
        (tmp_path / "sites").mkdir()
        path = tmp_path / "fm_config.toml"

        FMConfigManager.import_from_toml(path).export_to_toml(path)

        assert FMConfigManager.import_from_toml(path).get_system_migration_version() == Version(self._fm_version())
