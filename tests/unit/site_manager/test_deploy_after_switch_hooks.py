"""`after_switch` fires once at the end of every switch, carrying `DEPLOY_OUTCOME`.

One terminal hook, four mutually-exclusive outcomes -- `succeeded`, `rolled_back`, `halted`,
`aborted` -- so an operator can branch in one place: notify on success, page on `halted`, and (the
external-DB case) restore their own snapshot on `rolled_back`. It fires from `deploy`'s finally on
EVERY exit, must NOT mask the failure that triggered it, and an unconfigured hook is a real no-op.

`ROLLBACK_TO_IMAGE`, not `ROLLBACK_IMAGE`: `hook_env` already exports the `[switch].rollback_image`
bool under that name, so the rolled-back image needs its own -- pinned by the real-env test below,
which the runner-mocking tests cannot see.
"""

import shlex
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import (
    BenchRuntime,
    DeployState,
    SwitchConfig,
    SwitchHooks,
    SwitchHookScripts,
)
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator

SITE = "shop.localhost"
NEW = "reg.example/shop:v2"
OLD = "reg.example/shop:v1"

# after_switch is skipped when unconfigured (real no-op), so every firing test must wire it.
HOOKS = SwitchHooks(after_switch="container-hook", host=SwitchHookScripts(after_switch="host-hook"))

SPIED = (
    "_fetch_image",
    "_snapshot_compose",
    "_restore_compose",
    "_unwind_maintenance",
    "_pin_workers",
    "_set_maintenance",
    "drain_workers",
    "resume_workers",
    "_backup_all",
    "_restore_db",
    "_migrate",
    "_notify_after_migrate",
    "_run_host_hook",
    "_run_container_hook",
    "_rolling_swap",
    "_up_workers",
    "_health_check",
    "_ensure_nginx",
    "_install_new_apps",
    "_apply_config_merges",
    "_exec_frappe",
    "_record",
    "rollback",
    "prune_releases",
)


def _switch(**kw):
    return SwitchConfig(hooks=HOOKS, **kw)


def _orch(tmp_path, switch, deploy_state=None):
    config = SimpleNamespace(
        runtime=BenchRuntime.image,
        image=NEW,
        switch=switch,
        workers=None,
        root_path=str(tmp_path),
        deploy_state=deploy_state,
        apps_list=[],
        seed_image=None,
        base_image=None,
        redis=None,
        site_names=[SITE],
        export_to_toml=MagicMock(),
    )
    bench = SimpleNamespace(
        name=SITE,
        site_name=SITE,
        primary_domain=SITE,
        domains=[SITE],
        path=tmp_path,
        bench_config=config,
        docker_client=MagicMock(),
        compose_file_manager=MagicMock(),
        docker_ops=MagicMock(),
        workers=MagicMock(),
        unmanaged_site_dirs=MagicMock(return_value=[]),
    )
    orch = DeployOrchestrator(bench, output_handler=MagicMock())
    for name in SPIED:
        setattr(orch, name, MagicMock(name=name))
    orch._snapshot_compose.return_value = {"snap": b"x"}
    orch._backup_all.return_value = {}
    orch.drain_workers.return_value = True
    orch._health_check.return_value = True
    orch._frappe_running = MagicMock(return_value=True)
    return orch


def _fail_migrate(orch):
    orch._migrate.side_effect = DockerException(["docker"], SubprocessOutput(["boom"], ["boom"], ["boom"], 1))


def _env(orch):
    """The extra_env passed to the host after_switch runner, or None if it never fired."""
    for c in orch._run_host_hook.call_args_list:
        if c.args[1] == "host_after_switch":
            return c.kwargs.get("extra_env")
    return None


def _fired(orch):
    return {
        c.args[1]
        for m in (orch._run_container_hook, orch._run_host_hook)
        for c in m.call_args_list
        if c.args[1] in ("after_switch", "host_after_switch")
    }


def test_clean_switch_fires_succeeded(tmp_path):
    orch = _orch(tmp_path, _switch(migrate=False), deploy_state=DeployState(current_image=OLD))

    orch.deploy(NEW)

    assert _fired(orch) == {"after_switch", "host_after_switch"}
    env = _env(orch)
    assert env["DEPLOY_OUTCOME"] == "succeeded"
    assert "FAILED_IMAGE" not in env  # nothing failed
    assert "ROLLBACK_TO_IMAGE" not in env


def test_migrate_failure_fires_rolled_back(tmp_path):
    orch = _orch(tmp_path, _switch(migrate=True), deploy_state=DeployState(current_image=OLD))
    _fail_migrate(orch)

    with pytest.raises(DeployError, match="Migration failed"):
        orch.deploy(NEW)

    env = _env(orch)
    assert env["DEPLOY_OUTCOME"] == "rolled_back"
    assert env["ROLLBACK_REASON"] == "migrate_failed"
    assert env["FAILED_IMAGE"] == NEW
    assert env["ROLLBACK_TO_IMAGE"] == OLD  # old image still live (no swap)


def test_health_gate_failure_fires_rolled_back(tmp_path):
    orch = _orch(tmp_path, _switch(migrate=False), deploy_state=DeployState(current_image=OLD))
    orch._health_check.return_value = False

    with pytest.raises(DeployError, match="failed health check"):
        orch.deploy(NEW)

    orch.rollback.assert_called_once()  # fm re-pinned the previous image
    env = _env(orch)
    assert env["DEPLOY_OUTCOME"] == "rolled_back"
    assert env["ROLLBACK_REASON"] == "health_check_failed"
    assert env["ROLLBACK_TO_IMAGE"] == OLD


def test_health_gate_failure_with_no_previous_image_fires_halted(tmp_path):
    """No previous image -> the new (unhealthy) image stays pinned; new code on new schema is
    matched, so this is `halted` (page it), NOT a rollback (do not restore the DB here)."""
    orch = _orch(tmp_path, _switch(migrate=False), deploy_state=None)
    orch._health_check.return_value = False

    with pytest.raises(DeployError, match="halted in maintenance"):
        orch.deploy(NEW)

    orch.rollback.assert_not_called()
    env = _env(orch)
    assert env["DEPLOY_OUTCOME"] == "halted"
    assert env["FAILED_IMAGE"] == NEW
    assert "ROLLBACK_TO_IMAGE" not in env  # nothing was rolled back


def test_pre_change_failure_fires_aborted(tmp_path):
    """A drain-gate timeout aborts before anything changes -- schema untouched, old stack live."""
    orch = _orch(tmp_path, _switch(migrate=True), deploy_state=DeployState(current_image=OLD))
    orch.drain_workers.return_value = False

    with pytest.raises(DeployError, match="Drain timed out"):
        orch.deploy(NEW)

    env = _env(orch)
    assert env["DEPLOY_OUTCOME"] == "aborted"
    assert "ROLLBACK_TO_IMAGE" not in env  # no schema change -> nothing to restore


def test_unconfigured_after_switch_is_a_no_op(tmp_path):
    orch = _orch(tmp_path, SwitchConfig(migrate=False), deploy_state=DeployState(current_image=OLD))

    orch.deploy(NEW)

    assert _fired(orch) == set()  # skipped, runner never invoked


def test_a_broken_after_switch_hook_never_masks_the_failure(tmp_path):
    orch = _orch(tmp_path, _switch(migrate=True), deploy_state=DeployState(current_image=OLD))
    _fail_migrate(orch)

    def boom(_value, phase, _image, extra_env=None):
        if phase == "host_after_switch":
            raise DeployError("hook blew up")

    orch._run_host_hook.side_effect = boom

    with pytest.raises(DeployError, match="Migration failed"):
        orch.deploy(NEW)
    warnings = " ".join(str(c.args) for c in orch.output.warning.call_args_list)
    assert "hook failed" in warnings


def test_rollback_env_not_clobbered_by_switch_config_scalars(tmp_path):
    """hook_env also exports every SwitchConfig scalar upper-cased, so `rollback_image` exports as
    ROLLBACK_IMAGE. The rolled-back image travels as ROLLBACK_TO_IMAGE and must survive. Exercises
    the REAL `_hook_script` (the firing tests mock the runner and cannot see the exported env)."""
    orch = _orch(tmp_path, SwitchConfig(rollback_image=True), deploy_state=DeployState(current_image=OLD))

    script = orch._hook_script(
        "true",
        NEW,
        extra_env={"DEPLOY_OUTCOME": "rolled_back", "ROLLBACK_TO_IMAGE": OLD, "FAILED_IMAGE": NEW},
    )

    assert f"export DEPLOY_OUTCOME={shlex.quote('rolled_back')}" in script
    assert f"export ROLLBACK_TO_IMAGE={shlex.quote(OLD)}" in script
    assert "export ROLLBACK_IMAGE=true" in script  # the bool setting, under its own name
