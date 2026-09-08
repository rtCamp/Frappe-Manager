"""An unrecognised fm_config.toml key used to vanish with no signal: `import_from_toml` builds
its input from named `.get()` reads and `"x" in data` checks, so a misspelled top-level key or
table name (e.g. `[validaton]`) parsed cleanly and was simply never looked at. Every `fm` command
loads this file, so this warns rather than raises, matching the bench-side `BenchConfig` reader.
"""

from unittest.mock import MagicMock

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.output_manager import set_global_output_handler
from frappe_manager.output_manager.base import OutputHandler

_VERSION = 'version = "0.20.0.dev0"\n'


def _load_with_warnings(tmp_path, body: str):
    path = tmp_path / "fm_config.toml"
    path.write_text(_VERSION + body)

    handler = MagicMock(spec=OutputHandler)
    set_global_output_handler(handler)
    try:
        config = FMConfigManager.import_from_toml(path)
    finally:
        set_global_output_handler(None)
    return config, handler


def test_an_unknown_top_level_key_warns(tmp_path):
    config, handler = _load_with_warnings(tmp_path, 'typoed_kee = "x"\n')

    assert config.version.version == "0.20.0.dev0"  # loads regardless
    handler.warning.assert_called_once()
    assert "typoed_kee" in handler.warning.call_args.args[0]


def test_a_misspelled_table_name_warns(tmp_path):
    config, handler = _load_with_warnings(tmp_path, "[validaton]\nenforce_domain_uniqueness = false\n")

    # The misspelled table is never read, so the field keeps its default rather than the typo'd value.
    assert config.validation.enforce_domain_uniqueness is True
    handler.warning.assert_called_once()
    assert "validaton" in handler.warning.call_args.args[0]


def test_no_warning_for_an_ordinary_config(tmp_path):
    _, handler = _load_with_warnings(tmp_path, "[validation]\nenforce_domain_uniqueness = false\n")

    handler.warning.assert_not_called()


def test_the_legacy_cloudflare_table_does_not_warn(tmp_path):
    """`[cloudflare]` is a pre-0.20.0 table this reader still folds in by hand (see
    test_global_dns_credentials.py); it must stay a recognised spelling, not a typo."""
    _, handler = _load_with_warnings(tmp_path, '[cloudflare]\napi_key = "cf_LIVE"\n')

    handler.warning.assert_not_called()


def test_migration_state_does_not_warn(tmp_path):
    """`migration_state` is kept in `_raw_config`, not a pydantic field, but it is still a
    recognised top-level key."""
    _, handler = _load_with_warnings(tmp_path, '[migration_state]\nsystem_migrated_to = "0.19.0"\n')

    handler.warning.assert_not_called()
