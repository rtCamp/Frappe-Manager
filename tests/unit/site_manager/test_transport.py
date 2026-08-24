"""Unit tests for Phase 5 image transport helpers.

Covers env-substitution of registry creds: `[registry].username`/`password` may be
written as `${VAR}` so a bench_config.toml is safe to commit.
"""

from frappe_manager.site_manager.modules.transport import expand_env


def test_expand_env_substitutes(monkeypatch):
    monkeypatch.setenv("FM_REG_TOKEN", "s3cr3t")
    assert expand_env("${FM_REG_TOKEN}") == "s3cr3t"
    assert expand_env("$FM_REG_TOKEN") == "s3cr3t"
    assert expand_env(None) is None
    assert expand_env("plain") == "plain"
