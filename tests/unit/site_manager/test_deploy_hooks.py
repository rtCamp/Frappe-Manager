"""Contract tests for the shared hook helpers (#323).

Covers frappe_manager/site_manager/hooks.py used by both deploy switch-hooks
(DeployOrchestrator) and build hooks (provision): script resolution, env
construction, script assembly, and build-hook detection.
"""

from frappe_manager.site_manager.bench_config import DeployConfig
from frappe_manager.site_manager.hooks import (
    has_build_hooks,
    hook_env,
    hook_script,
    resolve_hook_content,
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
    env = hook_env(None, {"SITE_NAME": "x.localhost", "BENCH_PATH": "/data/x", "DEPLOY_TAG": "repo:t"})
    assert env["SITE_NAME"] == "x.localhost"
    assert env["BENCH_PATH"] == "/data/x"
    assert env["DEPLOY_TAG"] == "repo:t"


def test_hook_env_includes_deploy_fields_excludes_hooks():
    cfg = DeployConfig(
        image="ghcr.io/acme/x",
        migrate=False,
        after_restart="echo done",  # hook field: must NOT leak into env
        before_bench_build="echo build",  # hook field: must NOT leak
        common_site_config={"maintenance_mode": 1},
    )
    env = hook_env(cfg, {"BENCH_PATH": "/b"})
    assert env["IMAGE"] == "ghcr.io/acme/x"
    assert env["MIGRATE"] == "false"  # bool -> lowercased
    assert env["COMMON_SITE_CONFIG"] == '{"maintenance_mode": 1}'  # dict -> json
    assert "AFTER_RESTART" not in env
    assert "BEFORE_BENCH_BUILD" not in env


def test_hook_env_omits_none_fields():
    env = hook_env(DeployConfig(image="ghcr.io/acme/x"), {})
    assert "MIGRATE_COMMAND" not in env  # defaults None


def test_hook_script_structure():
    script = hook_script("echo hi", {"FOO": "bar baz"})
    assert script.startswith("set -e\n")
    assert "export FOO='bar baz'\n" in script  # shell-quoted
    assert script.endswith("echo hi")


def test_has_build_hooks_false_when_none_or_unset():
    assert has_build_hooks(None) is False
    assert has_build_hooks(DeployConfig(image="ghcr.io/acme/x")) is False
    # a switch hook alone is not a build hook
    assert has_build_hooks(DeployConfig(image="ghcr.io/acme/x", after_restart="echo x")) is False


def test_has_build_hooks_true_when_build_hook_set():
    assert has_build_hooks(DeployConfig(image="ghcr.io/acme/x", before_bench_build="echo b")) is True
    assert has_build_hooks(DeployConfig(image="ghcr.io/acme/x", host_after_python_install="echo p")) is True
