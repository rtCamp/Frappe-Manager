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

    assert config.get_system_migration_version().version == "0.20.0.dev0"  # loads regardless (ledger seeded from the retired top-level `version`)
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


def test_a_typo_inside_migration_state_warns(tmp_path):
    """The one blind spot the top-level/nested fixes above did not close: `[migration_state]` is
    kept as a raw dict in `_raw_config`, never a pydantic field, so `collect_unknown_keys` cannot
    walk into it structurally the way it does `[logs]`/`[validation]`/`[network]`/`[output]`. A
    typo there (e.g. `sytem_migrated_to`) used to parse cleanly and never be looked at again."""
    config, handler = _load_with_warnings(
        tmp_path, '[migration_state]\nsystem_migrated_to = "0.19.0"\nsytem_migrated_at = "typo-value"\n'
    )

    assert config.get_system_migration_version().version == "0.19.0"  # loads regardless (ledger seeded from the pre-rename `system_migrated_to`)
    handler.warning.assert_called_once()
    assert "migration_state.sytem_migrated_at" in handler.warning.call_args.args[0]


def test_a_typo_inside_logs_warns_instead_of_raising(tmp_path):
    """`FMLogsConfig` used to be `extra="forbid"`; a typo'd key there raised a `ValidationError`
    out of `import_from_toml`, which every `fm` command calls before the migration gate runs, so
    the typo broke the whole tool rather than one bench."""
    config, handler = _load_with_warnings(tmp_path, '[logs]\nfile_leveel = "DEBUG"\n')

    assert config.logs.file_level == "DEBUG"  # loads regardless, keeping the untouched default
    handler.warning.assert_called_once()
    assert "logs.file_leveel" in handler.warning.call_args.args[0]


def test_a_typo_inside_validation_warns_instead_of_raising(tmp_path):
    config, handler = _load_with_warnings(tmp_path, "[validation]\nenforce_domain_uniqueniss = false\n")

    assert config.validation.enforce_domain_uniqueness is True
    handler.warning.assert_called_once()
    assert "validation.enforce_domain_uniqueniss" in handler.warning.call_args.args[0]


def test_a_typo_inside_network_warns_instead_of_raising(tmp_path):
    config, handler = _load_with_warnings(tmp_path, '[network]\nsubnett_cidr = "10.1.0.0/16"\n')

    assert config.network.subnet_cidr is None
    handler.warning.assert_called_once()
    assert "network.subnett_cidr" in handler.warning.call_args.args[0]


def test_a_typo_inside_output_warns_instead_of_raising(tmp_path):
    config, handler = _load_with_warnings(tmp_path, '[output]\nthemee = "mono"\n')

    assert config.output.theme == "default"
    handler.warning.assert_called_once()
    assert "output.themee" in handler.warning.call_args.args[0]


def test_output_colors_dotted_keys_never_warn(tmp_path):
    """`[output.colors]` is `dict[str, str]` keyed by rich style tokens that already contain dots
    (e.g. `fm.env.prod`). Nothing under it is a `BaseModel`, so the collector must not walk into
    it, and it must never mistake a dotted key for a path to split."""
    _, handler = _load_with_warnings(
        tmp_path,
        '[output.colors]\n"fm.env.prod" = "bold magenta"\n"fm.env.dev" = "green"\n',
    )

    handler.warning.assert_not_called()


def test_top_level_and_nested_unknown_keys_are_one_message(tmp_path):
    """A typo at the top level and a typo inside a nested table are the same class of mistake to
    an operator, so they must be reported in a single warning naming both, not two separate ones."""
    config, handler = _load_with_warnings(
        tmp_path,
        'typoed_kee = "x"\n[network]\nsubnett_cidr = "10.1.0.0/16"\n',
    )

    assert config.get_system_migration_version().version == "0.20.0.dev0"  # loads regardless (ledger seeded from the retired top-level `version`)
    handler.warning.assert_called_once()
    message = handler.warning.call_args.args[0]
    assert "typoed_kee" in message
    assert "network.subnett_cidr" in message


def test_a_top_level_typo_is_named_exactly_once(tmp_path):
    """`top_level_unknown_keys` (used only to retain the stray onto `model_extra`) and
    `collect_unknown_keys` (which walks `fm_config_instance` afterwards) used to both name a
    top-level stray: `retained_top_level` puts it on `fm_config_instance`'s OWN `model_extra`
    before the walk starts, so the walk already finds it there. A hand-built list added the same
    bare name a second time, e.g. 'another_typo, ngrok_auth_tokenn, another_typo,
    ngrok_auth_tokenn' for two strays -- one typo, printed twice."""
    config, handler = _load_with_warnings(tmp_path, 'ngrok_auth_tokenn = "x"\n')

    assert config.get_system_migration_version().version == "0.20.0.dev0"  # loads regardless (ledger seeded from the retired top-level `version`)
    handler.warning.assert_called_once()
    message = handler.warning.call_args.args[0]
    assert message.count("ngrok_auth_tokenn") == 1, message


def test_two_top_level_and_one_nested_typo_are_one_sorted_deduplicated_message(tmp_path):
    """Two top-level strays plus one nested stray must land in exactly one warning, each name
    appearing exactly once, in sorted order -- not the top-level names doubled and concatenated
    ahead of the nested one unsorted."""
    config, handler = _load_with_warnings(
        tmp_path,
        'ngrok_auth_tokenn = "x"\nanother_typo = 1\n[network]\nsubnett_cidr = "10.1.0.0/16"\n',
    )

    assert config.get_system_migration_version().version == "0.20.0.dev0"  # loads regardless (ledger seeded from the retired top-level `version`)
    handler.warning.assert_called_once()
    message = handler.warning.call_args.args[0]
    names = message.split("unrecognised key(s) ", 1)[1].split("; check", 1)[0].split(", ")
    assert names == sorted(names)
    assert names == ["another_typo", "network.subnett_cidr", "ngrok_auth_tokenn"]
    for name in names:
        assert message.count(name) == 1, message


def test_top_level_nested_and_migration_state_typo_are_one_sorted_message(tmp_path):
    """`[migration_state]` is hand-checked (see `recognised_global_migration_state_keys`), the
    other two families are found structurally by `collect_unknown_keys` -- both routes must still
    land in the SAME `warn_or_log` call, sorted together, not a second warning for the hand-read
    one."""
    config, handler = _load_with_warnings(
        tmp_path,
        'ngrok_auth_tokenn = "x"\n'
        '[network]\nsubnett_cidr = "10.1.0.0/16"\n'
        '[migration_state]\nsystem_migrated_to = "0.19.0"\nsytem_migrated_at = "typo-value"\n',
    )

    assert config.get_system_migration_version().version == "0.19.0"  # loads regardless (ledger seeded from the pre-rename `system_migrated_to`)
    handler.warning.assert_called_once()
    message = handler.warning.call_args.args[0]
    names = message.split("unrecognised key(s) ", 1)[1].split("; check", 1)[0].split(", ")
    assert names == sorted(names)
    assert names == ["migration_state.sytem_migrated_at", "network.subnett_cidr", "ngrok_auth_tokenn"]
    for name in names:
        assert message.count(name) == 1, message
