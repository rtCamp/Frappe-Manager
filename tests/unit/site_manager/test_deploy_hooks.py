"""Contract tests for the shared hook helpers (#323).

Covers frappe_manager/site_manager/hooks.py used by both switch hooks
(DeployOrchestrator, from SwitchConfig) and per-app build hooks (provision, from
AppConfig.hooks): script resolution, env construction, script assembly, and
hook detection.
"""

from frappe_manager.site_manager.bench_config import (
    AppBuildHooks,
    BuildHookScripts,
    SwitchConfig,
    SwitchHooks,
    SwitchHookScripts,
)
from frappe_manager.site_manager.hooks import (
    app_has_build_hooks,
    hook_env,
    hook_script,
    resolve_hook_content,
    switch_has_hooks,
)


def test_inline_script_returned_verbatim():
    assert resolve_hook_content("echo hi && bench --site x clear-cache") == "echo hi && bench --site x clear-cache"


def test_path_like_but_missing_treated_as_inline():
    assert resolve_hook_content("./nope-not-here.sh") == "./nope-not-here.sh"


def test_existing_sh_file_is_read(tmp_path):
    script = tmp_path / "hook.sh"
    script.write_text("echo from-file\n")
    assert resolve_hook_content(str(script)) == "echo from-file\n"


def test_hook_env_core_vars_passed_through():
    env = hook_env({"SITE_NAME": "x.localhost", "BENCH_PATH": "/data/x", "DEPLOY_TAG": "repo:t"})
    assert env["SITE_NAME"] == "x.localhost"
    assert env["BENCH_PATH"] == "/data/x"
    assert env["DEPLOY_TAG"] == "repo:t"


def test_hook_env_includes_switch_scalars_excludes_hooks():
    cfg = SwitchConfig(
        migrate=False,
        common_site_config={"maintenance_mode": 1},
        hooks=SwitchHooks(after_restart="echo done"),  # nested hooks: must NOT leak into env
    )
    env = hook_env({"BENCH_PATH": "/b"}, cfg)
    assert env["MIGRATE"] == "false"  # bool -> lowercased
    assert env["COMMON_SITE_CONFIG"] == '{"maintenance_mode": 1}'  # dict -> json
    assert "HOOKS" not in env  # the nested hooks field is skipped by name
    assert "AFTER_RESTART" not in env  # a nested hook is not a scalar switch field


def test_hook_env_omits_none_fields():
    env = hook_env({}, SwitchConfig())
    assert "MIGRATE_COMMAND" not in env  # defaults None


def test_hook_script_structure():
    script = hook_script("echo hi", {"FOO": "bar baz"})
    assert script.startswith("set -e\n")
    assert "export FOO='bar baz'\n" in script  # shell-quoted
    assert script.endswith("echo hi")


def test_app_has_build_hooks_false_when_none_or_unset():
    assert app_has_build_hooks(None) is False
    assert app_has_build_hooks(AppBuildHooks()) is False


def test_app_has_build_hooks_true_when_set():
    assert app_has_build_hooks(AppBuildHooks(before_build="echo b")) is True
    # a host-side build hook alone still counts.
    assert app_has_build_hooks(AppBuildHooks(host=BuildHookScripts(after_build="echo p"))) is True


def test_switch_has_hooks_false_when_none_or_unset():
    assert switch_has_hooks(None) is False
    assert switch_has_hooks(SwitchHooks()) is False


def test_switch_has_hooks_true_when_set():
    assert switch_has_hooks(SwitchHooks(before_restart="echo r")) is True
    # a host-side switch hook alone still counts.
    assert switch_has_hooks(SwitchHooks(host=SwitchHookScripts(after_migrate="echo m"))) is True
