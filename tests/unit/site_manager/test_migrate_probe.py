"""migrate='auto' probe: marker parsing, config acceptance, rolling gate, hook env."""

from pathlib import Path

import pytest

from frappe_manager.site_manager.bench_config import SwitchConfig
from frappe_manager.site_manager.modules.deploy_orchestrator import (
    MIGRATE_PROBE_MARKER,
    DeployOrchestrator,
    parse_migrate_probe,
)

# ------------------------------------------------------------------ parsing


def test_parse_needed():
    r = parse_migrate_probe([f"{MIGRATE_PROBE_MARKER} needed pending=3 drift=none"])
    assert r == {"needed": True, "pending": 3, "drift": []}


def test_parse_clean():
    lines = ["console noise", f"{MIGRATE_PROBE_MARKER} clean pending=0 drift=none", "more noise"]
    assert parse_migrate_probe(lines) == {"needed": False, "pending": 0, "drift": []}


def test_parse_drift_apps():
    r = parse_migrate_probe([f"{MIGRATE_PROBE_MARKER} needed pending=0 drift=erpnext,hrms"])
    assert r["needed"] is True
    assert r["drift"] == ["erpnext", "hrms"]


def test_parse_no_verdict():
    assert parse_migrate_probe(["no marker here"]) is None
    assert parse_migrate_probe([]) is None
    assert parse_migrate_probe(None) is None


# ------------------------------------------------------------------ config


def test_config_accepts_auto():
    assert SwitchConfig(migrate="auto").migrate == "auto"
    assert SwitchConfig(migrate=True).migrate is True
    assert SwitchConfig(migrate=False).migrate is False


def test_config_rejects_typo_strings():
    # pydantic coerces boolish strings ("yes"/"true") to bool; a typo of "auto" must still fail.
    with pytest.raises(ValueError):
        SwitchConfig(migrate="atuo")
    assert SwitchConfig(migrate="yes").migrate is True  # boolish string coerces, documented


# ------------------------------------------------------------------ hook env


def _orch(probe=None, status=None, log=None):
    o = object.__new__(DeployOrchestrator)  # bypass __init__ (no bench/docker needed)
    o.site = "s.localhost"
    o.bench_path = Path("/b")
    o.switch_config = SwitchConfig()
    o._probe_result = probe  # noqa: SLF001
    o._migrate_status = status  # noqa: SLF001
    o._migrate_log_container = log  # noqa: SLF001
    o._migrate_log_host = Path("/b/workspace/frappe-bench/logs/m.log") if log else None  # noqa: SLF001
    return o


def test_hook_env_exports_probe_details():
    o = _orch({"needed": True, "pending": 3, "drift": ["erpnext"], "verdict": "needed"})
    script = o._hook_script("echo hi", "repo:t1")  # noqa: SLF001
    assert "export MIGRATE_PROBE=needed" in script
    assert "export MIGRATE_PENDING_PATCHES=3" in script
    assert "export MIGRATE_APP_DRIFT=erpnext" in script
    assert "export DEPLOY_TAG=repo:t1" in script


def test_hook_env_probe_unknowns():
    o = _orch({"needed": True, "pending": None, "drift": [], "verdict": "assumed-needed"})
    script = o._hook_script("echo hi", "repo:t1")  # noqa: SLF001
    assert "export MIGRATE_PROBE=assumed-needed" in script
    assert "export MIGRATE_PENDING_PATCHES=unknown" in script
    assert "export MIGRATE_APP_DRIFT=none" in script


def test_hook_env_without_probe_has_no_probe_vars():
    script = _orch()._hook_script("echo hi", "repo:t1")  # noqa: SLF001
    assert "MIGRATE_PROBE" not in script


def test_hook_env_exports_migrate_status_and_log():
    o = _orch(status="failed", log="/workspace/frappe-bench/logs/deploy-migrate-1.log")
    script = o._hook_script("echo hi", "repo:t1")  # noqa: SLF001
    assert "export MIGRATE_STATUS=failed" in script
    assert "export MIGRATE_LOG_FILE=/workspace/frappe-bench/logs/deploy-migrate-1.log" in script
    assert "export MIGRATE_LOG_FILE_HOST=/b/workspace/frappe-bench/logs/m.log" in script


def test_hook_env_without_migrate_has_no_status_vars():
    script = _orch()._hook_script("echo hi", "repo:t1")  # noqa: SLF001
    assert "MIGRATE_STATUS" not in script
    assert "MIGRATE_LOG_FILE" not in script
