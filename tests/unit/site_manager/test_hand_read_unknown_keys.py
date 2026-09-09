"""Phase 2: hand-read table reporting, plus the top-level extra="allow" asymmetry Phase 1 left.

`import_from_toml` reads `[ssl]`/`[deploy_state]`/`[sites]` by hand rather than splatting a whole
table straight into a pydantic model, so Phase 1's `collect_unknown_keys` (which only walks
`model_extra`) cannot see a stray key inside any of them on its own. The three tables differ in
what they CAN hold a stray key in, so the fix is not the same shape for all three:

- `[sites."<name>"]` IS a model (`SiteConfig`, `extra="allow"` since Phase 1) that the reader
  nonetheless built from explicit kwargs instead of splatting -- so a stray at a SITE's own top
  level was silently dropped despite the model being able to hold it. Fixed by splatting the
  unrecognised remainder into the constructor; `collect_unknown_keys` now finds it structurally,
  the same way it already found one inside a site's nested `[database]`/`[auth]` tables (those
  WERE already splatted, and already worked before this change).

- `[ssl]`/`[deploy_state]` have no model of their own for the table AS A WHOLE (`BenchConfig`
  holds `ssl_certificates`/`dns_providers`/`deploy_state` as separate, differently-shaped fields),
  so a stray there has nowhere to round-trip as `model_extra`. `BenchConfig.hand_read_unknown_keys()`
  reports these by hand, in the same dotted-path shape `collect_unknown_keys` produces, for a
  caller to combine with `collect_unknown_keys(config)`.

- `BenchConfig` itself used to be plain "ignore": a nested stray round-tripped (`extra="allow"`
  all the way down since Phase 1) while a top-level one was warned about once and then silently
  dropped forever -- two mistakes a user would call identical, behaving differently. Fixed by
  flipping `BenchConfig` to `extra="allow"` too and retaining the raw value: fm never deletes a
  key it does not understand, at any depth.
"""

from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.utils.config_keys import collect_unknown_keys

SITE = "shop.localhost"

_BASE = f'name = "{SITE}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'


def _import(tmp_path, text: str) -> BenchConfig:
    path = tmp_path / "bench_config.toml"
    path.write_text(text)
    return BenchConfig.import_from_toml(path)


class TestSiteTableStrayKeys:
    def test_a_top_level_stray_in_a_sites_own_table_is_retained_and_collectible(self, tmp_path):
        """The real bug: `SiteConfig` was built from explicit kwargs, so a stray at a site's own
        top level never reached the model at all, despite `SiteConfig` already being
        `extra="allow"`. The dotted path uses the site's own name (which itself contains a dot)
        as ONE path segment, never split on it -- exactly Phase 1's `dict[str, Model]` convention,
        and precisely why splitting a composite string would be wrong here.
        """
        cfg = _import(
            tmp_path,
            _BASE + f'\n[sites."{SITE}"]\nserve_admin_tools = true\ntypo_top_level = "boom"\n',
        )
        assert cfg.sites[SITE].model_extra == {"typo_top_level": "boom"}
        assert collect_unknown_keys(cfg) == [f"sites.{SITE}.typo_top_level"]

    def test_a_stray_inside_a_sites_nested_database_and_auth_tables_reports_the_full_path(self, tmp_path):
        cfg = _import(
            tmp_path,
            _BASE
            + f'\n[sites."{SITE}"]\n'
            + f'[sites."{SITE}".database]\nhost = "db"\nname = "app"\nprot = 3306\n'
            + f'[sites."{SITE}".auth]\nweb = true\nusr = "oops"\n',
        )
        assert collect_unknown_keys(cfg) == [
            f"sites.{SITE}.auth.usr",
            f"sites.{SITE}.database.prot",
        ]

    def test_the_sites_recognised_fields_are_unaffected_by_splatting_the_remainder(self, tmp_path):
        """Splatting the unrecognised remainder alongside the explicit database/alias_domains/
        auth/serve_admin_tools kwargs must not change what those four still resolve to."""
        cfg = _import(
            tmp_path,
            _BASE
            + f'\n[sites."{SITE}"]\n'
            + "serve_admin_tools = false\n"
            + 'alias_domains = ["www.shop.localhost"]\n'
            + f'[sites."{SITE}".database]\nhost = "db"\nname = "app"\n',
        )
        site = cfg.sites[SITE]
        assert site.serve_admin_tools is False
        assert site.alias_domains == ["www.shop.localhost"]
        assert site.database.host == "db"
        assert site.model_extra == {}


class TestHandReadTableStrays:
    def test_an_ssl_stray_is_reported_via_hand_read_unknown_keys(self, tmp_path):
        cfg = _import(tmp_path, _BASE + "\n[ssl]\ncertificatess = []\n")
        assert cfg.hand_read_unknown_keys() == ["ssl.certificatess"]
        # No SSLConfig model backs the [ssl] table as a whole, so it cannot show up structurally.
        assert collect_unknown_keys(cfg) == []

    def test_a_deploy_state_stray_is_now_found_structurally_not_via_hand_read(self, tmp_path):
        """Unlike `[ssl]`, `[deploy_state]` has a real model field (`DeployState`, extra="allow")
        to hold its remainder: retained there directly (see bench_config.py), so
        `collect_unknown_keys` finds it the same way it finds a `[switch]` stray, with no
        separate hand-list to keep in sync."""
        cfg = _import(
            tmp_path,
            _BASE + '\n[deploy_state]\ncurrent_image = "repo:tag"\nhistroy = []\n',
        )
        assert collect_unknown_keys(cfg) == ["deploy_state.histroy"]
        # [ssl] is the one hand-read table left with no model of its own for the whole table.
        assert cfg.hand_read_unknown_keys() == []

    def test_the_deploy_state_stale_tag_warning_is_not_also_reported_as_unknown(self, tmp_path):
        """`current_tag`/`previous_tag` (the pre-rename spellings) get their own dedicated stale-
        tag warning; they must not ALSO show up as a generic unrecognised key."""
        cfg = _import(tmp_path, _BASE + '\n[deploy_state]\ncurrent_tag = "v1"\n')
        assert cfg.hand_read_unknown_keys() == []
        assert collect_unknown_keys(cfg) == []

    def test_hand_read_unknown_keys_combines_with_the_collector_for_the_complete_picture(self, tmp_path):
        cfg = _import(
            tmp_path,
            _BASE
            + f'\n[sites."{SITE}"]\ntypo_top_level = "boom"\n'
            + "\n[ssl]\ncertificatess = []\n"
            + '\n[deploy_state]\ncurrent_image = "repo:tag"\nhistroy = []\n',
        )
        combined = sorted(collect_unknown_keys(cfg) + cfg.hand_read_unknown_keys())
        assert combined == [
            "deploy_state.histroy",
            f"sites.{SITE}.typo_top_level",
            "ssl.certificatess",
        ]



class TestOneWarningNamesEveryStray:
    """Phase 3: a top-level stray, one inside a splatted table, and one inside the one hand-read
    table left ([ssl]) are three different MECHANISMS (retained via `BenchConfig.model_extra`,
    via `SwitchConfig.model_extra`, and via `hand_read_unknown_keys()` respectively) but must
    produce exactly ONE message naming all three, not one warning per mechanism."""

    def test_a_top_level_a_table_and_a_hand_read_stray_produce_one_combined_warning(self, tmp_path):
        from unittest.mock import MagicMock

        from frappe_manager.output_manager import set_global_output_handler
        from frappe_manager.output_manager.base import OutputHandler
        from frappe_manager.utils.helpers import get_current_fm_version

        text = (
            _BASE
            + "top_level_typo = true\n"
            + "[switch]\ntable_typo = true\n"
            + "[ssl]\nhand_read_typo = []\n"
            + f'\n[migration_state]\nmigrated_to = "{get_current_fm_version()}"\n'
        )
        handler = MagicMock(spec=OutputHandler)
        set_global_output_handler(handler)
        try:
            _import(tmp_path, text)
        finally:
            set_global_output_handler(None)

        handler.warning.assert_called_once()
        message = handler.warning.call_args.args[0]
        for key in ("top_level_typo", "switch.table_typo", "ssl.hand_read_typo"):
            assert key in message

class TestLegacyConfigRetainsItsPreMigrationNames:
    """Top-level `alias_domains`/`database` and a top-level `[database."<site>"]` table (the
    pre-0.20 per-site database home, no `[sites]` table at all) are a genuine pre-migration
    bench_config.toml, not a typo. `RELOCATED_CONFIG_KEYS` used to exempt exactly these names
    from `collect_unknown_keys` entirely; Phase 5 removed that hand-list in favour of a version
    check on the WARNING (see test_bench_config_toml.py's `TestPreMigrationBenchConfigNeverWarns`
    for the "no warning" half), so these names are now retained and collectible like any other
    stray -- strictly better, since a fifth pre-migration name never added to a hand-list cannot
    be missed the way these four almost were.
    """

    def test_a_0_19_shaped_config_retains_its_pre_migration_names(self, tmp_path):
        legacy = (
            _BASE
            + 'alias_domains = ["www.shop.example.com"]\n'
            + f'\n[database."{SITE}"]\n'
            + 'host = "rds.internal"\n'
            + "port = 3307\n"
            + 'name = "app_prod"\n'
        )
        cfg = _import(tmp_path, legacy)
        assert collect_unknown_keys(cfg) == ["alias_domains", "database"]
        assert cfg.hand_read_unknown_keys() == []


class TestTopLevelAsymmetryFixed:
    """`BenchConfig` used to be plain "ignore": a nested stray round-tripped (`extra="allow"` all
    the way down, since Phase 1) while a top-level one was warned about once and then vanished on
    the very next save. Decision: retain it too, for losslessness -- fm never deletes a key it does
    not understand, and a user who mistypes at the top level and one who mistypes inside a table
    made the identical mistake, so they get identical treatment (warn, and keep the evidence)."""

    def test_a_top_level_stray_is_retained_instead_of_dropped(self, tmp_path):
        cfg = _import(tmp_path, _BASE + 'nonexistent_toplevel_key = "oops"\n')
        assert cfg.model_extra == {"nonexistent_toplevel_key": "oops"}
        assert collect_unknown_keys(cfg) == ["nonexistent_toplevel_key"]

    def test_the_retained_top_level_stray_survives_a_two_cycle_export_reimport_fixed_point(self, tmp_path):
        """Retention is proved against the ORIGINAL source, not cycle-to-cycle: a key dropped on
        the very first save would make `first_text == second_text` trivially true (both exports
        already lack it), so that alone is not evidence of anything. The fixed point is a SEPARATE
        property (no further drift once the stray settles), the same bar Phase 1 proved for a
        nested stray (see test_bench_config_toml.py's switch_typo_key case)."""
        path = tmp_path / "bench_config.toml"
        path.write_text(_BASE + 'nonexistent_toplevel_key = "oops"\n')

        first_out = tmp_path / "first.toml"
        BenchConfig.import_from_toml(path).export_to_toml(first_out)
        first_text = first_out.read_text()

        second_out = tmp_path / "second.toml"
        BenchConfig.import_from_toml(first_out).export_to_toml(second_out)
        second_text = second_out.read_text()

        assert 'nonexistent_toplevel_key = "oops"' in first_text, "must survive against the ORIGINAL"
        assert first_text == second_text, "no further drift once retained"


class TestMigrationStateVerdict:
    """`MigrationState` was the one model in this file Phase 1's forbid -> allow sweep missed: it
    was never `extra="forbid"` to begin with (no `model_config` at all), so it fell outside that
    sweep's search-and-replace scope, and was left at pydantic's default `extra="ignore"` --
    worse than `forbid` here, since `ignore` neither raises nor round-trips, it just deletes a
    stray key silently on the very next save. Fixed here (not deferred) because it is a one-line
    consistency fix inside this owned file, not because `[migration_state]` is hand-read like the
    other three -- it is already splatted (`MigrationState(**migration_state_data)`), so flipping
    the flag is the entire fix and `collect_unknown_keys` finds it with no reader change at all.
    """

    def test_a_migration_state_stray_is_retained_instead_of_silently_dropped(self, tmp_path):
        cfg = _import(
            tmp_path,
            _BASE + '\n[migration_state]\nmigrated_to = "0.19.0"\nmigrated_at = "2026-01-01"\n',
        )
        assert cfg.migration_state.migrated_to == "0.19.0"
        assert collect_unknown_keys(cfg) == ["migration_state.migrated_at"]
