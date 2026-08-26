"""Saving a config file keeps what the reader wrote in it, and still retires what the model dropped.

`export_to_toml` used to build a fresh document and overwrite the file, so every comment died on the
next save. The 0.20.0 migration preserves comments with a tomlkit round-trip and then
`set_bench_migration_version` stamped the version through the model and deleted them again, which is
how this was found.

Both halves are pinned here. Preservation on its own would be a regression: the old overwrite is what
made a removed key actually disappear, so a merge that only ever adds would leave retired keys and
deleted tables on disk forever, and the migration would stop meaning anything.
"""

import tomlkit

from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.utils import toml_document

_BENCH = 'name = "x.localhost"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'


def _saved(path, body: str) -> str:
    path.write_text(body)
    BenchConfig.import_from_toml(path).export_to_toml(path)
    return path.read_text()


def test_a_comment_survives_a_save(tmp_path):
    text = _saved(tmp_path / "bench_config.toml", "# owner: platform team\n" + _BENCH)

    assert "# owner: platform team" in text


def test_a_comment_inside_a_table_survives_a_change_to_its_neighbour(tmp_path):
    path = tmp_path / "bench_config.toml"
    path.write_text(_BENCH + "\n[switch]\n# raised after the March incident\nmigrate_timeout = 900\n")

    config = BenchConfig.import_from_toml(path)
    config.upload_limit = "200M"
    config.export_to_toml(path)

    text = path.read_text()
    assert "# raised after the March incident" in text
    assert 'upload_limit = "200M"' in text
    assert "900" in text


def test_a_key_the_model_no_longer_produces_is_removed(tmp_path):
    """The half that keeps a merge honest. Without it a retired key would live on disk forever."""
    text = _saved(tmp_path / "bench_config.toml", _BENCH + 'registry_leftover = "stale"\n\n[registry]\nuser = "gone"\n')

    assert "registry_leftover" not in text
    assert "[registry]" not in text


def test_retired_certificate_keys_do_not_come_back(tmp_path):
    body = _BENCH + (
        '\n[[ssl.certificates]]\ndomain = "x.localhost"\nssl_type = "letsencrypt"\n'
        'api_key = "LEAKED"\nstatus = "pending"\ncert_path = "/x"\n'
    )

    text = _saved(tmp_path / "bench_config.toml", body)

    assert "LEAKED" not in text
    assert "status" not in text
    assert "cert_path" not in text


def test_saving_twice_changes_nothing(tmp_path):
    """A save that is not a fixed point would rewrite every bench file on every command."""
    path = tmp_path / "bench_config.toml"
    once = _saved(path, "# keep me\n" + _BENCH)

    BenchConfig.import_from_toml(path).export_to_toml(path)

    assert path.read_text() == once


def test_the_global_config_keeps_comments_and_still_retires_the_old_table(tmp_path):
    path = tmp_path / "fm_config.toml"
    path.write_text('# host notes\nversion = "0.20.0.dev0"\n[cloudflare]\napi_key = "cf_LIVE"\n')

    FMConfigManager.import_from_toml(path).export_to_toml(path)

    text = path.read_text()
    assert "# host notes" in text
    assert "[cloudflare]" not in text
    assert "cf_LIVE" in text


def test_an_unparseable_file_does_not_block_the_write(tmp_path):
    """Refusing to save because of a syntax error elsewhere in the file would be worse than losing
    its comments, and the overwrite this replaced never had that failure mode."""
    path = tmp_path / "bench_config.toml"
    path.write_text("not = = toml [[[\n")

    assert toml_document.load_or_new(path) == tomlkit.document()


def test_a_new_scalar_does_not_fall_under_an_existing_table(tmp_path):
    """The hazard of merging: a bare key appended after a table header belongs to that table on the
    next read, which would silently move config into the wrong place."""
    doc = tomlkit.parse('name = "a"\n\n[switch]\nmigrate = true\n')

    toml_document.apply(doc, {"name": "a", "added": "V", "switch": {"migrate": True}})

    text = tomlkit.dumps(doc)
    assert text.index("added") < text.index("[switch]")
    assert tomlkit.parse(text)["added"] == "V"
