"""`on_rollback` hooks fire whenever fm rolls a failed deploy back.

Two rollback paths, one contract: a failed `bench migrate` (old image kept, no swap) and a
post-swap health-gate image rollback both fire `on_rollback` -- container then host -- with
ROLLBACK_REASON / FAILED_IMAGE / ROLLBACK_TO_IMAGE in the hook env, on top of the usual
MIGRATE_STATUS. This is the seam an external-DB operator uses to restore their own snapshot in
lock-step with fm's image rollback (fm only ever restores its own logical dumps), so it MUST fire
on both paths, MUST NOT fire on a clean deploy or when fm cannot roll back at all (halted, no
previous image), and MUST NOT mask the failure that triggered it.
"""

import shlex
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.bench_config import BenchRuntime, DeployState, SwitchConfig
from frappe_manager.site_manager.modules.deploy_orchestrator import DeployError, DeployOrchestrator

SITE = "shop.localhost"
NEW = "reg.example/shop:v2"
OLD = "reg.example/shop:v1"

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


def _failed_migrate(orch):
    orch._migrate.side_effect = DockerException(["docker"], SubprocessOutput(["boom"], ["boom"], ["boom"], 1))


def _rollback_env(orch):
    """The extra_env passed to the host on_rollback runner, or None if it never fired."""
    for c in orch._run_host_hook.call_args_list:
        if c.args[1] == "host_on_rollback":
            return c.kwargs.get("extra_env")
    return None


def _phases(orch):
    return {c.args[1] for m in (orch._run_container_hook, orch._run_host_hook) for c in m.call_args_list}


def test_migrate_failure_fires_on_rollback_both_locations(tmp_path):
    orch = _orch(tmp_path, SwitchConfig(migrate=True), deploy_state=DeployState(current_image=OLD))
    _failed_migrate(orch)

    with pytest.raises(DeployError, match="Migration failed"):
        orch.deploy(NEW)

    assert {"on_rollback", "host_on_rollback"} <= _phases(orch)
    env = _rollback_env(orch)
    assert env["ROLLBACK_REASON"] == "migrate_failed"
    assert env["FAILED_IMAGE"] == NEW
    assert env["ROLLBACK_TO_IMAGE"] == OLD  # the image still live (no swap happened)


def test_health_gate_failure_fires_on_rollback_after_image_rollback(tmp_path):
    orch = _orch(tmp_path, SwitchConfig(migrate=False), deploy_state=DeployState(current_image=OLD))
    orch._health_check.return_value = False

    with pytest.raises(DeployError, match="failed health check"):
        orch.deploy(NEW)

    orch.rollback.assert_called_once()  # fm re-pinned the previous image
    env = _rollback_env(orch)
    assert env["ROLLBACK_REASON"] == "health_check_failed"
    assert env["FAILED_IMAGE"] == NEW
    assert env["ROLLBACK_TO_IMAGE"] == OLD


def test_clean_deploy_does_not_fire_on_rollback(tmp_path):
    orch = _orch(tmp_path, SwitchConfig(migrate=False), deploy_state=DeployState(current_image=OLD))

    orch.deploy(NEW)

    # before/after_restart hooks still fire; on_rollback must not.
    assert "on_rollback" not in _phases(orch)
    assert "host_on_rollback" not in _phases(orch)


def test_halt_with_no_previous_image_does_not_fire_on_rollback(tmp_path):
    """No previous image means fm cannot roll back -- it halts in maintenance instead. That is not
    a rollback, so the hook must not fire (its whole premise is 'we reverted to an earlier state')."""
    orch = _orch(tmp_path, SwitchConfig(migrate=False), deploy_state=None)
    orch._health_check.return_value = False

    with pytest.raises(DeployError, match="halted in maintenance"):
        orch.deploy(NEW)

    orch.rollback.assert_not_called()
    assert "host_on_rollback" not in _phases(orch)


def test_a_broken_on_rollback_hook_never_masks_the_deploy_failure(tmp_path):
    orch = _orch(tmp_path, SwitchConfig(migrate=True), deploy_state=DeployState(current_image=OLD))
    _failed_migrate(orch)

    def boom(_value, phase, _deploy_image, extra_env=None):
        if phase == "host_on_rollback":
            raise DeployError("hook blew up")

    orch._run_host_hook.side_effect = boom

    # The migrate error is what surfaces, not the hook error.
    with pytest.raises(DeployError, match="Migration failed"):
        orch.deploy(NEW)

    warnings = " ".join(str(c.args) for c in orch.output.warning.call_args_list)
    assert "rollback path" in warnings


def test_rollback_env_is_not_clobbered_by_the_switch_config_scalar_dump(tmp_path):
    """hook_env also exports every SwitchConfig scalar upper-cased, so `rollback_image` (the bool
    setting) exports as ROLLBACK_IMAGE. The rollback TARGET image therefore travels as
    ROLLBACK_TO_IMAGE -- a distinct name -- and must reach the hook uncorrupted. Exercises the
    REAL `_hook_script`/`hook_env` (the firing tests above mock the runner, so they cannot see a
    name collision in the exported env)."""
    orch = _orch(tmp_path, SwitchConfig(rollback_image=True), deploy_state=DeployState(current_image=OLD))

    script = orch._hook_script(
        "true",
        NEW,
        extra_env={"ROLLBACK_REASON": "migrate_failed", "FAILED_IMAGE": NEW, "ROLLBACK_TO_IMAGE": OLD},
    )

    assert f"export ROLLBACK_TO_IMAGE={shlex.quote(OLD)}" in script
    assert f"export FAILED_IMAGE={shlex.quote(NEW)}" in script
    # the bool setting still exports under its own name, distinct from the target image
    assert "export ROLLBACK_IMAGE=true" in script
