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
)
from frappe_manager.site_manager.hooks import (
    app_has_build_hooks,
    hook_env,
    hook_script,
    resolve_hook_content,
)
from frappe_manager.utils.config_keys import collect_unknown_keys


def test_inline_script_returned_verbatim():
    assert resolve_hook_content("echo hi && bench --site x clear-cache") == "echo hi && bench --site x clear-cache"


def test_path_like_but_missing_treated_as_inline():
    assert resolve_hook_content("./nope-not-here.sh") == "./nope-not-here.sh"


def test_existing_sh_file_is_read(tmp_path):
    script = tmp_path / "hook.sh"
    script.write_text("echo from-file\n")
    assert resolve_hook_content(str(script)) == "echo from-file\n"


def test_hook_env_core_vars_passed_through():
    env = hook_env({"SITE_NAME": "x.localhost", "BENCH_PATH": "/data/x", "DEPLOY_IMAGE": "repo:t"})
    assert env["SITE_NAME"] == "x.localhost"
    assert env["BENCH_PATH"] == "/data/x"
    assert env["DEPLOY_IMAGE"] == "repo:t"


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


def test_app_has_build_hooks_ignores_a_stray_named_host_on_the_bare_base_class():
    """`host` is declared only on `AppBuildHooks`, not its own base `BuildHookScripts`. A stray
    `host` retained (`extra="allow"`) on a bare `BuildHookScripts` must never be treated as the
    real nested hook-scripts sub-model -- established empirically, not reasoned: a raw dict or
    string has no `before_deps`/etc. attribute either way, so this never raises and never returns
    a false positive, but it must resolve through `declared_field`, not the stray, since a future
    caller reading the SAME `host` value one field deeper must see None, not someone else's data.
    """
    stray_dict_host = BuildHookScripts.model_validate({"host": {"before_deps": "echo host"}})
    stray_string_host = BuildHookScripts.model_validate({"host": "not-a-real-host-block"})

    assert collect_unknown_keys(stray_dict_host) == ["host"]
    assert collect_unknown_keys(stray_string_host) == ["host"]
    # Neither stray shape is ever treated as "hooks configured": the gate that decides whether
    # fm's build-hooks pipeline (and therefore a subprocess) runs at all stays False.
    assert app_has_build_hooks(stray_dict_host) is False
    assert app_has_build_hooks(stray_string_host) is False


def test_a_misspelled_hook_name_inside_host_is_retained_but_never_read_as_a_hook():
    """The case that would turn a typo into executed code: a stray key inside a REAL, properly
    typed `[hooks.host]` table that merely LOOKS like a hook name. Both `app_has_build_hooks`
    (the gate deciding whether the hook pipeline, and therefore a subprocess, runs at all) and
    the actual script read in `provisioner.py` (`host.before_deps`, a literal attribute name)
    only ever consult the four real, hardcoded field names -- never whatever key the operator
    typed -- so a misspelling can only ever be silently inert, never executed.
    """
    host = BuildHookScripts.model_validate({"before_depss": "echo malicious"})
    hooks = AppBuildHooks(host=host)

    assert collect_unknown_keys(hooks) == ["host.before_depss"]
    # The typo alone never flips the gate: nothing declared is actually set.
    assert app_has_build_hooks(hooks) is False
    # The real field it was meant to be remains genuinely unset, not silently filled by the typo.
    assert host.before_deps is None

    # A genuine hook alongside the typo: the gate now reflects the REAL hook, and the typo still
    # never surfaces as anything `hook_script`/a subprocess would ever see.
    host_with_real_hook = BuildHookScripts.model_validate({"before_deps": "echo real", "before_depss": "echo malicious"})
    hooks_with_real_hook = AppBuildHooks(host=host_with_real_hook)
    assert app_has_build_hooks(hooks_with_real_hook) is True
    assert host_with_real_hook.before_deps == "echo real"
    assert collect_unknown_keys(hooks_with_real_hook) == ["host.before_depss"]
