"""Contract tests for --config overlays (#323).

Each --config value is a file path or inline TOML; multiple overlays deep-merge
left-to-right (later wins) into the bench config, persisted.
"""

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
    merged = merge_overlays('[switch]\nmigrate = true\nbackups = true\n', ['[switch]\nmigrate = false\n'])
    doc = tomlkit.parse(merged)
    assert doc["switch"]["migrate"] is False  # overridden
    assert doc["switch"]["backups"] is True  # deep-merge preserved the sibling


def test_multiple_overlays_apply_in_order():
    merged = merge_overlays('image = "a"\n', ['image = "b"\n', 'image = "c"'])
    assert tomlkit.parse(merged)["image"] == "c"  # last --config wins


def test_overlay_adds_new_table():
    merged = merge_overlays('name = "x"\n', ['[build]\npython_version = "3.12"\n'])
    assert tomlkit.parse(merged)["build"]["python_version"] == "3.12"


def test_list_value_overwrites_not_appends():
    merged = merge_overlays('[switch]\nmaintenance_mode_phases = ["migrate"]\n', ['[switch]\nmaintenance_mode_phases = []\n'])
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
