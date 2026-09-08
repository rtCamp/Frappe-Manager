"""Contract tests for --config overlays (#323).

Each --config value is a file path or inline TOML; multiple overlays deep-merge
left-to-right (later wins) into the bench config, persisted.
"""

import re

import pytest
import tomlkit

from frappe_manager.site_manager.deploy_config_overlay import (
    ConfigOverlayError,
    apply_config_overlays,
    merge_overlays,
    resolve_source,
)


def test_resolve_source_inline_passthrough():
    assert resolve_source('image = "x"') == 'image = "x"'


def test_resolve_source_reads_file(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('image = "x"\n')
    assert resolve_source(str(f)) == 'image = "x"\n'


def test_deep_merge_later_wins_preserves_siblings():
    merged = merge_overlays("[switch]\nmigrate = true\nbackup_db = true\n", ["[switch]\nmigrate = false\n"])
    doc = tomlkit.parse(merged)
    assert doc["switch"]["migrate"] is False  # overridden
    assert doc["switch"]["backup_db"] is True  # deep-merge preserved the sibling


def test_multiple_overlays_apply_in_order():
    merged = merge_overlays('image = "a"\n', ['image = "b"\n', 'image = "c"'])
    assert tomlkit.parse(merged)["image"] == "c"  # last --config wins


def test_overlay_adds_new_table():
    merged = merge_overlays('name = "x"\n', ['[build]\npython_version = "3.12"\n'])
    assert tomlkit.parse(merged)["build"]["python_version"] == "3.12"


def test_list_value_overwrites_not_appends():
    merged = merge_overlays(
        '[switch]\nmaintenance_mode_phases = ["migrate"]\n', ["[switch]\nmaintenance_mode_phases = []\n"]
    )
    assert tomlkit.parse(merged)["switch"]["maintenance_mode_phases"] == []


def test_apply_persists_file_then_inline(tmp_path):
    bench = tmp_path / "bench_config.toml"
    bench.write_text('name = "x"\nimage = "a"\n[switch]\nmigrate = true\n')
    override = tmp_path / "override.toml"
    override.write_text('image = "b"\n')

    apply_config_overlays(bench, [str(override), "switch.migrate = false"])

    doc = tomlkit.parse(bench.read_text())
    assert doc["image"] == "b"  # from the file overlay
    assert doc["switch"]["migrate"] is False  # from the later inline overlay
    assert doc["name"] == "x"  # untouched base key


def test_apply_empty_is_noop(tmp_path):
    bench = tmp_path / "bench_config.toml"
    bench.write_text('name = "x"\n')
    apply_config_overlays(bench, [])
    assert bench.read_text() == 'name = "x"\n'


def test_apply_missing_bench_raises(tmp_path):
    with pytest.raises(ConfigOverlayError):
        apply_config_overlays(tmp_path / "nope.toml", ['image = "x"'])


def test_invalid_toml_raises(tmp_path):
    bench = tmp_path / "bench_config.toml"
    bench.write_text('name = "x"\n')
    with pytest.raises(ConfigOverlayError):
        apply_config_overlays(bench, ["[[[not valid"])


def test_an_unknown_top_level_key_is_refused():
    """The same silent-drop hazard `BenchConfig.import_from_toml` warns about instead: this seam
    serves one interactive command with an operator present, so it refuses outright."""
    with pytest.raises(ConfigOverlayError, match="typoed_kee"):
        merge_overlays('name = "x"\n', ["typoed_kee = true"])


def test_an_unknown_deploy_state_key_is_refused():
    with pytest.raises(ConfigOverlayError, match=re.escape("deploy_state.curent_image")):
        merge_overlays('name = "x"\n', ['[deploy_state]\ncurent_image = "v2"\n'])


def test_an_unknown_ssl_key_is_refused():
    """`[ssl]` is the third hand-read table (`bench_config.py:1799-1815` reads
    `certificates`/`dns_providers` by hand), so a typo there must be caught here too, matching
    `[deploy_state]` above -- not silently dropped when `fm bake --config` writes it to disk."""
    with pytest.raises(ConfigOverlayError, match=re.escape("ssl.certificatess")):
        merge_overlays('name = "x"\n', ["[ssl]\ncertificatess = []\n"])


def test_a_recognised_ssl_key_is_not_refused():
    merged = merge_overlays('name = "x"\n', ["[ssl]\ncertificates = []\n"])
    assert tomlkit.parse(merged)["ssl"]["certificates"] == []


def test_a_retired_table_is_not_refused():
    """`[registry]` is gone from the model but still tolerated on load; the overlay seam must
    agree, not refuse a bench config that `BenchConfig.import_from_toml` itself accepts."""
    merged = merge_overlays('name = "x"\n', ['[registry]\nregistry = "ghcr.io/acme"\n'])
    assert tomlkit.parse(merged)["registry"]["registry"] == "ghcr.io/acme"


def test_a_relocated_key_is_not_refused():
    """`alias_domains` is a top-level name `migrate_0_20_0` relocates (not retires); the overlay
    seam must agree with `recognised_bench_config_keys()`, same as the retired-table case above,
    or `fm bake --config` would refuse a value fm's own pre-migration files carry."""
    merged = merge_overlays('name = "x"\n', ['alias_domains = ["a.example.com"]\n'])
    assert tomlkit.parse(merged)["alias_domains"] == ["a.example.com"]


def test_apply_persists_nothing_when_an_overlay_is_refused(tmp_path):
    """A refused overlay must not partially land on disk."""
    bench = tmp_path / "bench_config.toml"
    original = 'name = "x"\nimage = "a"\n'
    bench.write_text(original)

    with pytest.raises(ConfigOverlayError):
        apply_config_overlays(bench, ['image = "b"', "typoed_kee = true"])

    assert bench.read_text() == original
