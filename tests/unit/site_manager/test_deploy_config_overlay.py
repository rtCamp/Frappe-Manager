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


def test_a_retired_table_is_refused():
    """`[registry]` is gone from the model but still tolerated on a plain READ (retained and
    version-gated, the same as any other stray). The `--config` overlay seam is a DIFFERENT
    origin: a key the human is typing right now, so severity-by-origin refuses it here even
    though a bench that already carries it on disk loads without complaint."""
    with pytest.raises(ConfigOverlayError, match="registry"):
        merge_overlays('name = "x"\n', ['[registry]\nregistry = "ghcr.io/acme"\n'])


def test_a_relocated_key_is_refused():
    """`alias_domains` used to be exempted here via `RELOCATED_CONFIG_KEYS`, tolerated because a
    pre-migration bench legitimately carries it at the top level. That list is gone (Phase 5): a
    `--config` overlay is authored NOW, at the current schema, where the top level is not where
    `alias_domains` belongs (`[sites."<name>"].alias_domains` is), so this origin refuses it even
    though the same name loaded from an old FILE would only warn."""
    with pytest.raises(ConfigOverlayError, match="alias_domains"):
        merge_overlays('name = "x"\n', ['alias_domains = ["a.example.com"]\n'])


def test_a_switch_table_stray_is_refused():
    """The shallow top-level-plus-two-tables check used to miss this entirely: `SwitchConfig` is
    `extra="allow"`, so a stray inside `[switch]` was retained rather than raising, and nothing
    here ever looked past the top level and `[ssl]`/`[deploy_state]` to notice. Running the
    merged document through `BenchConfig`'s own collector (see `_refused_keys`) is what catches
    it now."""
    with pytest.raises(ConfigOverlayError, match=r"switch\.typoed_stray"):
        merge_overlays('name = "x"\n', ["[switch]\ntypoed_stray = true\n"])


def test_apply_persists_nothing_when_an_overlay_is_refused(tmp_path):
    """A refused overlay must not partially land on disk."""
    bench = tmp_path / "bench_config.toml"
    original = 'name = "x"\nimage = "a"\n'
    bench.write_text(original)

    with pytest.raises(ConfigOverlayError):
        apply_config_overlays(bench, ['image = "b"', "typoed_kee = true"])

    assert bench.read_text() == original


def test_apply_succeeds_on_pre_migration_base_and_overlay_still_lands(tmp_path):
    """1.0.0 is unreleased, so every bench on disk right now still carries pre-migration shape:
    a top-level `alias_domains` and a `[registry]` table `BenchConfig` no longer recognises. The
    old bug ran the refusal check on the WHOLE merged document, so these pre-existing strays got
    blamed on whatever `--config` value happened to be applied, refusing a bake this seam exists
    to allow. The refusal must look only at what THIS overlay contributed."""
    bench = tmp_path / "bench_config.toml"
    bench.write_text('name = "x"\nalias_domains = ["a.example.com"]\n\n[registry]\nregistry = "ghcr.io/acme"\n')

    apply_config_overlays(bench, ['image = "x"'])

    doc = tomlkit.parse(bench.read_text())
    assert doc["image"] == "x"  # the overlay landed
    assert doc["alias_domains"] == ["a.example.com"]  # pre-migration stray still retained
    assert doc["registry"]["registry"] == "ghcr.io/acme"  # pre-migration table still retained


def test_a_stray_already_on_disk_is_not_refused():
    """The same pre-migration strays as above, but through `merge_overlays` directly: an overlay
    that never touches `alias_domains`/`[registry]` must not be refused because of them -- their
    origin is the file, not this invocation, so a plain read of the same file would only warn."""
    base = 'name = "x"\nalias_domains = ["a.example.com"]\n\n[registry]\nregistry = "ghcr.io/acme"\n'
    merged = merge_overlays(base, ['image = "x"'])
    doc = tomlkit.parse(merged)
    assert doc["image"] == "x"
    assert doc["alias_domains"] == ["a.example.com"]
    assert doc["registry"]["registry"] == "ghcr.io/acme"


def test_a_stray_the_overlay_introduces_is_still_refused_key_named(tmp_path):
    """A pre-migration base already carries unrelated strays; the overlay introduces a genuinely
    NEW one. Only the new key is named -- the diff against the base's unknown set must not let
    pre-existing strays drown out or get confused with what this overlay actually added."""
    base = 'name = "x"\nalias_domains = ["a.example.com"]\n\n[registry]\nregistry = "ghcr.io/acme"\n'
    with pytest.raises(ConfigOverlayError, match=re.escape("typoed_kee")) as excinfo:
        merge_overlays(base, ["typoed_kee = true"])
    assert "alias_domains" not in str(excinfo.value)
    assert "registry" not in str(excinfo.value)


def test_an_overlay_resetting_an_already_unknown_keys_value_is_not_refused():
    """Edge case: the operator's overlay DOES type this key, but the key's name was already
    unrecognised on disk before this overlay touched it -- only its value is new. Severity here
    tracks whether the NAME is new, not who last supplied the value: a plain read of the base file
    already retains-and-warns about this same name regardless of who wrote it, so refusing it here
    just because this invocation happened to repeat it would make the refusal stricter than the
    warning it exists to agree with."""
    base = 'name = "x"\nalias_domains = ["a.example.com"]\n'
    merged = merge_overlays(base, ['alias_domains = ["b.example.com"]'])
    assert tomlkit.parse(merged)["alias_domains"] == ["b.example.com"]  # overlay's value won


def test_an_overlay_stray_beside_a_preexisting_different_stray_is_refused_precisely():
    """Edge case: the base already has one stray inside `[switch]`; the overlay adds a DIFFERENT
    stray inside the same table. `_refused_keys` reports dotted per-field paths, not per-table
    membership, so the pre-existing stray and the newly-introduced one are distinct set members
    and the diff isolates exactly the one this overlay added."""
    base = 'name = "x"\n\n[switch]\nold_typo = true\n'
    with pytest.raises(ConfigOverlayError, match=re.escape("switch.new_typo")) as excinfo:
        merge_overlays(base, ["[switch]\nnew_typo = true\n"])
    assert "old_typo" not in str(excinfo.value)


@pytest.mark.parametrize(
    "overlay_text",
    [
        pytest.param("ssl = 5", id="scalar-where-ssl-table-belongs"),
        pytest.param("switch = 5", id="scalar-where-switch-table-belongs"),
        pytest.param("sites = 5", id="scalar-where-sites-table-belongs"),
        pytest.param("ssl = [1, 2, 3]", id="list-where-ssl-table-belongs"),
        pytest.param('switch = "x"', id="string-where-switch-table-belongs"),
        pytest.param("[sites.mysite]\ndatabase = 5\n", id="nested-wrong-shape-sites-database"),
    ],
)
def test_a_hostile_overlay_shape_is_refused_not_a_traceback(overlay_text):
    """`_refused_keys` runs the real reader, which does `.get`/`.items()`/`dict(...)` assuming a
    dict; a hostile overlay can hand it a scalar, list, or string instead, and the old guard only
    caught pydantic's `ValidationError` -- these shapes crash before validation is ever reached,
    as plain `AttributeError`/`TypeError`/`ValueError`, and escaped as a traceback instead of this
    seam's `ConfigOverlayError`."""
    with pytest.raises(ConfigOverlayError):
        merge_overlays('name = "x"\n', [overlay_text])


def test_an_empty_table_overlay_is_not_a_hostile_shape():
    """Sanity check for the widened guard: an empty `[ssl]` table is a valid (if pointless)
    overlay, not a hostile shape, and must not be swept up by the wider exception net."""
    merged = merge_overlays('name = "x"\n', ["[ssl]\n"])
    assert "ssl" in tomlkit.parse(merged)
