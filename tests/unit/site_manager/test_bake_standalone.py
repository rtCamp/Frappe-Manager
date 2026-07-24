"""Contract tests for standalone `fm bake` (bench-less build).

`_build_standalone_config` turns --apps/--image/--python/--node/--github-token +
--config overlays into a transient BenchConfig (no bench dir). BakeManager then
bakes from that explicit apps_list instead of deriving one from a live bench.
"""

import pytest

from frappe_manager.commands.bake import _bake_name, _build_standalone_config
from frappe_manager.site_manager.bench_config import AppConfig
from frappe_manager.site_manager.modules.bake import BakeError, BakeManager


def _apps(*specs: str) -> list[AppConfig]:
    return [AppConfig.from_string(s) for s in specs]


def test_bake_name_from_image_repo():
    assert _bake_name("ghcr.io/acme/mysite") == "mysite"
    assert _bake_name("localhost:5000/acme/mysite") == "mysite"  # host:port not mistaken for tag
    assert _bake_name("ghcr.io/acme/mysite:tag") == "mysite"
    assert _bake_name(None) == "fm-bake"


def test_flags_build_config_with_frappe_first():
    bc = _build_standalone_config(_apps("erpnext:version-15"), "ghcr.io/acme/x", "3.12", "20", "gh_tok", [])
    names = [a.name for a in bc.apps_list]
    assert names[0] == "frappe"  # frappe auto-added + first
    assert "erpnext" in names
    assert bc.build is not None
    assert bc.build.python_version == "3.12"
    assert bc.build.node_version == "20"
    assert bc.github_token == "gh_tok"  # noqa: S105


def test_explicit_frappe_not_duplicated():
    bc = _build_standalone_config(_apps("frappe:version-15", "erpnext:version-15"), "r/x", None, None, None, [])
    assert [a.name for a in bc.apps_list].count("frappe") == 1
    assert bc.apps_list[0].name == "frappe"
    assert bc.apps_list[0].ref == "version-15"  # user's frappe branch preserved
    assert bc.build is None  # no --python/--node


def test_config_overlay_supplies_image_and_apps():
    inline = """
image = "ghcr.io/acme/fromconfig"
[[apps]]
name = "frappe"
repo = "frappe/frappe"
ref = "version-16"
"""
    bc = _build_standalone_config([], None, None, None, None, [inline])
    assert bc.image is not None
    assert bc.image == "ghcr.io/acme/fromconfig"
    assert [a.name for a in bc.apps_list] == ["frappe"]


def test_config_overlay_wins_over_flag_build():
    # --python seeds [build]; a later --config overlay overrides it (later wins).
    inline = '[build]\npython_version = "3.11"\n'
    bc = _build_standalone_config(_apps("erpnext"), "r/x", "3.12", None, None, [inline])
    assert bc.build.python_version == "3.11"


def test_resolve_bake_apps_prefers_explicit_list():
    bc = _build_standalone_config(_apps("erpnext:version-15"), "r/x", None, None, None, [])
    manager = BakeManager(bc, output_handler=None)
    resolved = manager._resolve_bake_apps()  # noqa: SLF001
    assert [a.name for a in resolved] == [a.name for a in bc.apps_list]


def test_resolve_bake_apps_derives_when_empty(tmp_path):
    # Empty apps_list + no live bench dir -> derivation fails (bench-mode contract).
    inline = 'image = "r/x"\n'
    bc = _build_standalone_config([], None, None, None, None, [inline])
    bc.root_path = tmp_path / "bench_config.toml"  # parent has no workspace/apps
    manager = BakeManager(bc, output_handler=None)
    with pytest.raises(BakeError):
        manager._resolve_bake_apps()  # noqa: SLF001
