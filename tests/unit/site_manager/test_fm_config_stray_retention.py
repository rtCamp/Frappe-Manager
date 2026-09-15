"""A warning that fires once and then the evidence disappears is worse than no warning: the
operator is told to check for a typo, then the very next ordinary write (`set_system_migration_version`
fires on every `fm migrate`) erases the mistyped line, because `import_from_toml` used to build
`input_data` by hand and never named an unrecognised top-level key, so it never reached the model
`export_to_toml` dumps into `desired`, and `toml_document.apply`'s prune deleted anything not in
`desired`. `FMConfigManager` is now `extra="allow"` and merges `retained_top_level` onto
`input_data`, the same shape `BenchConfig.retained_top_level` uses.

Every retention assertion here is proven against the file as ORIGINALLY written, not cycle to
cycle: if a key were silently dropped on cycle one, cycle one and cycle two would be byte-identical
and a fixed-point assertion would pass for the wrong reason.
"""

import datetime

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.utils.config_keys import collect_unknown_keys

_VERSION = 'version = "0.20.0"\n'


def _config(tmp_path, body: str):
    path = tmp_path / "fm_config.toml"
    path.write_text(_VERSION + body)
    return path


def test_a_top_level_stray_survives_a_load_then_save(tmp_path):
    path = _config(tmp_path, 'ngrok_auth_tokenn = "SECRET-TOKEN"\n')

    FMConfigManager.import_from_toml(path).export_to_toml(path)

    text = path.read_text()
    assert "ngrok_auth_tokenn" in text
    assert "SECRET-TOKEN" in text


def test_a_top_level_stray_survives_two_saves(tmp_path):
    """The routine case: `fm migrate` writes via `set_system_migration_version`, then writes
    again later. Neither write may be the one that finally erases the typo."""
    path = _config(tmp_path, 'ngrok_auth_tokenn = "SECRET-TOKEN"\n')

    FMConfigManager.import_from_toml(path).export_to_toml(path)
    FMConfigManager.import_from_toml(path).export_to_toml(path)

    text = path.read_text()
    assert "ngrok_auth_tokenn" in text
    assert "SECRET-TOKEN" in text


def test_a_top_level_stray_round_trips_back_into_the_model(tmp_path):
    """Not just present in the file: `model_extra` carries it, the same way a nested stray does,
    so `collect_unknown_keys` keeps finding it on every subsequent load."""
    path = _config(tmp_path, 'ngrok_auth_tokenn = "SECRET-TOKEN"\n')

    config = FMConfigManager.import_from_toml(path)

    assert config.model_extra == {"ngrok_auth_tokenn": "SECRET-TOKEN"}


def test_a_stray_inside_logs_survives_a_save(tmp_path):
    """The Phase 4 case (`FMLogsConfig` flipped to `extra="allow"`): confirm it was not also
    broken by leaving `FMConfigManager` itself on the default `extra="ignore"`."""
    path = _config(tmp_path, '[logs]\nfile_leveel = "DEBUG"\n')

    FMConfigManager.import_from_toml(path).export_to_toml(path)

    text = path.read_text()
    assert "file_leveel" in text


def test_the_legacy_cloudflare_table_still_folds_and_still_warns_of_nothing(tmp_path):
    from unittest.mock import MagicMock

    from frappe_manager.output_manager import set_global_output_handler
    from frappe_manager.output_manager.base import OutputHandler

    path = _config(tmp_path, '[cloudflare]\nemail = "ops@example.com"\napi_key = "cf_LIVE"\n')

    handler = MagicMock(spec=OutputHandler)
    set_global_output_handler(handler)
    try:
        config = FMConfigManager.import_from_toml(path)
    finally:
        set_global_output_handler(None)

    entry = (config.dns_providers or {})["cloudflare"]
    assert entry.api_key == "cf_LIVE"
    assert entry.email == "ops@example.com"
    handler.warning.assert_not_called()


def test_a_stray_inside_the_legacy_cloudflare_table_is_retained_and_warned(tmp_path):
    """The gap the top-level fix alone did not close: the fold used to build `DNSProviderConfig`
    from three named keyword arguments, so a typo inside `[cloudflare]` (e.g. `api_toekn`) was
    never even passed to the model -- not counted as an unknown top-level key (`cloudflare` is a
    recognised top-level key) and not retained as `model_extra` either. Now splatted whole, so
    `DNSProviderConfig`'s own `extra="allow"` catches it."""
    from unittest.mock import MagicMock

    from frappe_manager.output_manager import set_global_output_handler
    from frappe_manager.output_manager.base import OutputHandler

    path = _config(tmp_path, '[cloudflare]\napi_key = "cf_LIVE"\napi_toekn = "typo-value"\n')

    handler = MagicMock(spec=OutputHandler)
    set_global_output_handler(handler)
    try:
        config = FMConfigManager.import_from_toml(path)
    finally:
        set_global_output_handler(None)

    entry = (config.dns_providers or {})["cloudflare"]
    assert entry.model_extra == {"api_toekn": "typo-value"}
    handler.warning.assert_called_once()
    assert "dns_providers.cloudflare.api_toekn" in handler.warning.call_args.args[0]

    config.export_to_toml(path)
    text = path.read_text()
    assert "typo-value" in text
    assert "cf_LIVE" in text


def test_a_cloudflare_table_with_only_a_typo_and_no_real_credential_still_survives(tmp_path):
    """The extreme case: no `api_token`/`api_key` at all, only the typo. Before this fix the whole
    entry -- `legacy_entry.exists` was False -- was silently discarded, both on the fold itself and
    (independently) by `export_to_toml`'s own `provider_config.exists` write gate."""
    path = _config(tmp_path, '[cloudflare]\napi_toekn = "cf_LIVE"\n')

    config = FMConfigManager.import_from_toml(path)
    entry = (config.dns_providers or {})["cloudflare"]
    assert entry.model_extra == {"api_toekn": "cf_LIVE"}

    config.export_to_toml(path)
    text = path.read_text()
    assert "cf_LIVE" in text
    assert "api_toekn" in text


def test_a_stray_inside_a_direct_dns_provider_entry_survives_a_save(tmp_path):
    """Item 4, `[ssl].dns_providers`: a stray key inside an already-current
    `[ssl.dns_providers.<label>]` entry was retained on import (`DNSProviderConfig` is
    `extra="allow"`) but silently dropped by `export_to_toml`'s `provider_config.exists` gate
    whenever the entry held no real `api_token`/`api_key` -- the write path turned the collector's
    warning into a lie by deleting the very key it just warned about."""
    path = _config(tmp_path, '[ssl.dns_providers.mylabel]\napi_toekn = "SECRET-TOKEN"\n')

    config = FMConfigManager.import_from_toml(path)
    assert config.dns_providers["mylabel"].model_extra == {"api_toekn": "SECRET-TOKEN"}

    config.export_to_toml(path)
    text = path.read_text()
    assert "SECRET-TOKEN" in text
    assert "api_toekn" in text


def test_an_empty_dns_provider_table_still_grows_no_label(tmp_path):
    """The other half of the same gate: a table with no real credentials AND no stray key must
    still not grow an empty label -- this is not the bug, only the case the fix must not break."""
    path = _config(tmp_path, '[ssl.dns_providers.mylabel]\nemail = "ops@example.com"\n')

    config = FMConfigManager.import_from_toml(path)
    config.export_to_toml(path)

    assert "[ssl" not in path.read_text()


def test_migration_state_keeps_every_key_across_a_save(tmp_path):
    """Item 4, `[migration_state]`: kept as a whole-table JSON round-trip in `_raw_config`, not a
    hand-picked field extraction, so it was already lossless before this fix -- pinned here rather
    than left to the fix's own test coverage."""
    path = _config(
        tmp_path,
        '[migration_state]\nsystem_migrated_to = "0.19.0"\nnotes = "manual override, do not touch"\n',
    )

    FMConfigManager.import_from_toml(path).export_to_toml(path)

    text = path.read_text()
    assert 'system_migrated_to = "0.19.0"' in text
    assert "manual override, do not touch" in text


def test_migration_state_keeps_every_key_across_two_saves(tmp_path):
    """The stray now also gets a warning (test_fm_config_unrecognised_keys.py's
    `test_a_typo_inside_migration_state_warns`), but the retention guarantee was never
    contingent on recognition: proven against the file as ORIGINALLY written, then again after a
    SECOND ordinary write -- the routine repeated `set_system_migration_version`
    sequence a real host takes -- since a two-cycle fixed point alone would not catch a value
    dropped on the very first cycle."""
    path = _config(
        tmp_path,
        '[migration_state]\nsystem_migrated_to = "0.19.0"\nnotes = "manual override, do not touch"\n',
    )
    original = path.read_text()
    assert 'system_migrated_to = "0.19.0"' in original
    assert "manual override, do not touch" in original

    FMConfigManager.import_from_toml(path).export_to_toml(path)
    FMConfigManager.import_from_toml(path).export_to_toml(path)

    text = path.read_text()
    assert 'system_migrated_to = "0.19.0"' in text
    assert "manual override, do not touch" in text


def test_set_system_migration_version_write_does_not_drop_a_top_level_stray(tmp_path):
    """The other routine trigger: every `fm migrate` calls this."""
    from frappe_manager.migration_manager.version import Version

    path = _config(tmp_path, 'ngrok_auth_tokenn = "SECRET-TOKEN"\n[migration_state]\nsystem_migrated_to = "0.19.0"\n')

    config = FMConfigManager.import_from_toml(path)
    config.set_system_migration_version(Version("0.20.0"))

    text = path.read_text()
    assert "ngrok_auth_tokenn" in text
    assert "SECRET-TOKEN" in text


def test_a_top_level_stray_of_every_awkward_toml_shape_survives_a_save_against_the_original(tmp_path):
    """The global-config counterpart of the bench-side test in `test_bench_config_toml.py`:
    `unwrap_toml_value`'s top-level remainder call site here is a SEPARATE call site from the
    bench-side one (different module, no shared import path between the two), so the same shape
    round-tripping on `BenchConfig` proves nothing about `FMConfigManager`'s own call site.

    Proven against the ORIGINAL text (hand-authored, never machine-generated, so a pass here is
    not a trivial fixed point) and by EXACT type on the reloaded model: a value still wrapped in
    a tomlkit `Item` would pass every text assertion below unchanged (tomlkit's `Item` types
    already subclass their plain equivalents), so only the type check would catch
    `unwrap_toml_value` reverted to a no-op.

    Only the DATA is asserted stable across a second load/save, not the exact bytes: a top-level
    array-of-tables stray gets one extra blank line on its first export here (inside
    `FMConfigManager.export_to_toml`'s AoT handling) that a second export does not reproduce --
    a pre-existing whitespace quirk unrelated to `unwrap_toml_value` (confirmed by the
    byte-identical two-cycle result the bench-side equivalent test gets for the same shape; the
    two readers do not share this code path). No key or value is lost either cycle.
    """
    path = _config(
        tmp_path,
        'stray_str = "hello \\"world\\" quoted"\n'
        "stray_dt = 1979-05-27T07:32:00Z\n"
        '"stray.dotted.key" = "dotted-value"\n'
        "\n[stray_sub]\na = 1\nb = 'nested'\n"
        "\n[[stray_aot]]\nx = 1\n[[stray_aot]]\nx = 2\n",
    )
    original = path.read_text()
    markers = ("stray_str", "hello", "stray_dt", "1979-05-27", "stray.dotted.key", "stray_sub", "stray_aot")
    for marker in markers:
        assert marker in original

    FMConfigManager.import_from_toml(path).export_to_toml(path)
    first = path.read_text()
    for marker in markers:
        assert marker in first, f"{marker!r} must survive against the ORIGINAL, not just cycle-to-cycle"

    extra = FMConfigManager.import_from_toml(path).model_extra or {}
    assert type(extra["stray_str"]) is str
    assert extra["stray_str"] == 'hello "world" quoted'
    assert type(extra["stray_dt"]) is datetime.datetime
    assert extra["stray_dt"] == datetime.datetime(1979, 5, 27, 7, 32, tzinfo=datetime.UTC)
    assert type(extra["stray.dotted.key"]) is str
    assert extra["stray.dotted.key"] == "dotted-value"
    assert type(extra["stray_sub"]) is dict
    assert extra["stray_sub"] == {"a": 1, "b": "nested"}
    assert type(extra["stray_aot"]) is list
    assert extra["stray_aot"] == [{"x": 1}, {"x": 2}]
    assert collect_unknown_keys(FMConfigManager.import_from_toml(path)) == sorted(
        ["stray_str", "stray_dt", "stray.dotted.key", "stray_sub", "stray_aot"]
    )

    # A second cycle must not lose or corrupt the DATA, even though the AoT quirk above changes
    # one blank line's worth of formatting.
    FMConfigManager.import_from_toml(path).export_to_toml(path)
    second_extra = FMConfigManager.import_from_toml(path).model_extra or {}
    assert second_extra == extra
