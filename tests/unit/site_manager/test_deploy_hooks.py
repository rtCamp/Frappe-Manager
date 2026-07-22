"""Contract tests for deploy switch-hook helpers (#323).

Covers the pure helpers behind DeployOrchestrator's before/after-restart hooks:
- _resolve_hook_content: inline script vs. path-to-file resolution.
- _hook_env: core vars + [deploy] fields upper-cased, hook fields excluded.

The container/host execution + pipeline placement are exercised by the remote
e2e; these lock the resolution + env contract hooks rely on.
"""

from frappe_manager.site_manager.bench_config import DeployConfig
from frappe_manager.site_manager.modules.deploy_orchestrator import (
    _hook_env,
    _resolve_hook_content,
)


def test_inline_script_returned_verbatim():
    assert _resolve_hook_content("echo hi && bench --site x clear-cache") == "echo hi && bench --site x clear-cache"


def test_path_like_but_missing_treated_as_inline():
    # Looks like a path (.sh) but does not exist -> treat as inline script text.
    assert _resolve_hook_content("./nope-not-here.sh") == "./nope-not-here.sh"


def test_existing_sh_file_is_read(tmp_path):
    script = tmp_path / "hook.sh"
    script.write_text("echo from-file\n")
    assert _resolve_hook_content(str(script)) == "echo from-file\n"


def test_hook_env_core_vars_present():
    env = _hook_env(None, site="x.localhost", bench_path="/data/x", deploy_tag="repo:tag1")
    assert env["SITE_NAME"] == "x.localhost"
    assert env["BENCH_PATH"] == "/data/x"
    assert env["DEPLOY_TAG"] == "repo:tag1"


def test_hook_env_includes_deploy_fields_excludes_hooks():
    cfg = DeployConfig(
        image="ghcr.io/acme/x",
        migrate=False,
        after_restart="echo done",  # a hook field: must NOT leak into env
        common_site_config={"maintenance_mode": 1},
    )
    env = _hook_env(cfg, site="x", bench_path="/b", deploy_tag="t")
    assert env["IMAGE"] == "ghcr.io/acme/x"
    assert env["MIGRATE"] == "false"  # bool -> lowercased
    assert env["COMMON_SITE_CONFIG"] == '{"maintenance_mode": 1}'  # dict -> json
    assert "AFTER_RESTART" not in env  # hook script fields are excluded
    assert "BEFORE_RESTART" not in env


def test_hook_env_omits_none_fields():
    cfg = DeployConfig(image="ghcr.io/acme/x")  # migrate_command defaults None
    env = _hook_env(cfg, site="x", bench_path="/b", deploy_tag="t")
    assert "MIGRATE_COMMAND" not in env
