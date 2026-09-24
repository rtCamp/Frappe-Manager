"""`[migration_state].migrated_to` is the ONE version key in fm_config.toml.

Before P2 there were two (`system_migrated_to` and the top-level `version`), read by different
consumers (the gates read the first, the executor's discovery read the second) and stamped from
three places -- the state that produces "gate says migrate, executor says nothing to do".
Legacy spellings are understood in exactly one place, `import_from_toml`'s seed, because the
ledger is read BEFORE any migration can run: without the seed a v1.0.0 host reads 0.0.0 and
discovery re-selects the frozen v0.19/v1.0.0 migrations against it. The write path owns the
disk cutover: the first stamp pops `system_migrated_to` and the export prune retires `version`.
"""

from pathlib import Path

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
    """The write path leaves exactly one version key on disk."""

    def test_first_stamp_on_a_legacy_file_leaves_exactly_one_version_key(self, tmp_path):
        path = _config(tmp_path, 'version = "1.0.0"\n[migration_state]\nsystem_migrated_to = "1.0.0"\n')

        config = FMConfigManager.import_from_toml(path)
        config.set_system_migration_version(Version("1.0.1"))

        text = path.read_text()
        assert 'migrated_to = "1.0.1"' in text
        assert "system_migrated_to" not in text
        assert "version =" not in text  # retired by the export prune

    def test_an_ordinary_save_retires_the_version_key_but_never_invents_a_ledger(self, tmp_path):
        """`version` is pruned like `[cloudflare]` was; the seeded ledger value is persisted
        under the current spelling, so the file converges without a stamp."""
        path = _config(tmp_path, 'version = "1.0.0"\n')

        FMConfigManager.import_from_toml(path).export_to_toml(path)

        text = path.read_text()
        assert "version =" not in text
        assert 'migrated_to = "1.0.0"' in text
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

        assert 'migrated_to = "1.0.1"' in path.read_text()
        assert decoy.read_text() == ""
